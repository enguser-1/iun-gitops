"""
iun-uin-bridge v3.0 (2026-07-14) — Birth + Death events, enregistrement synchrone.
- UIN : 10 digits Verhoeff -> SN-XXXX-XXXX-XX
- BRN Birth : RRR-YYYY-NNNNNN
- DRN Death : RRR-YYYY-DNNNNNN (D prefix pour distinguer Death)
- v2.1 : detection region via DISTRICT
- v2.2 : ecrit ALSO national-id pour {{nationalId}}
- v2.3 : endpoint /certificate/birth/{patient_id}
- v2.4 : QR + /records + tracking + informant
- v2.5 : gestion Death events, mint UIN si absent + DRN. Endpoint /certificate/death/{id}
- v2.6 : fix noms (given vides -> double espace), layout cert (Delivre a vs Officier)
- v2.7 : design officiel SRMT (serif, vert forêt, or, filigrane baobab) — cert + /records
- v3.0 : endpoint synchrone POST /event-registration (point d'extension OpenCRVS, route nginx
  du mock countryconfig) : mint UIN + BRN/DRN AU MOMENT de l'enregistrement puis mutation
  confirmRegistration vers le gateway (registrationNumber = BRN officiel DKR-YYYY-NNNNNN,
  identifiers = BIRTH_CONFIGURABLE_IDENTIFIER_1 = UIN) -> l'IUN et le BRN apparaissent sur
  l'acte natif ({{birthConfigurableIdentifier1}} / {{registrationNumber}}).
  Idempotence retry : db.iun_event_reg (_id = compositionId).
  Le poller devient RECONCILIATEUR : adopte l'UIN v3 (copie vers UIN_SYSTEM + national-id
  pour /records et le cert bridge), et continue de traiter les dossiers pre-v3.
- Idempotence : presence UIN_SYSTEM
- Sequences : brn:{region}:{year} et drn:{region}:{year} dans db.iun_counters
"""

import asyncio
import base64
import io
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import segno
from fastapi import FastAPI, Request
from pymongo import MongoClient, ReturnDocument

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("uin-bridge")

MONGO_URL = os.environ["MONGO_URL"]
HEARTH_URL = os.environ.get("HEARTH_URL", "http://hearth:3447")
UIN_SERVICE_URL = os.environ.get(
    "UIN_SERVICE_URL",
    "http://iun-uin-service.iun-mosip-dev.svc.cluster.local:8080",
)
POLL_INTERVAL_S = int(os.environ.get("POLL_INTERVAL_S", "30"))
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("1", "true", "yes")

UIN_SYSTEM = "http://iun.sn/specs/id/uin"
UIN_TYPE_CODE = "UIN"
BRN_SYSTEM = "http://opencrvs.org/specs/id/birth-registration-number"
BRN_TYPE_CODE = "BIRTH_REGISTRATION_NUMBER"
DRN_SYSTEM = "http://opencrvs.org/specs/id/death-registration-number"
DRN_TYPE_CODE = "DEATH_REGISTRATION_NUMBER"
NATIONAL_ID_SYSTEM = "http://opencrvs.org/specs/id/national-id"
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:7070")
BCID1_TYPE_CODE = "BIRTH_CONFIGURABLE_IDENTIFIER_1"
BCID1_SYSTEM = "http://opencrvs.org/specs/id/birth-configurable-identifier-1"
REG_STATUS_REGISTERED = "REGISTERED"

EVENT_BIRTH = "birth-declaration"
EVENT_DEATH = "death-declaration"

REGION_CODES = {
    "dakar": "DKR",
    "thies": "THS", "thiès": "THS",
    "saint-louis": "STL", "saint louis": "STL",
    "kaolack": "KLK",
    "diourbel": "DBL",
    "louga": "LGA",
    "fatick": "FCK",
    "tambacounda": "TMB",
    "kolda": "KLD",
    "ziguinchor": "ZGR",
    "matam": "MTM",
    "kaffrine": "KFF",
    "kedougou": "KDG", "kédougou": "KDG",
    "sedhiou": "SDH", "sédhiou": "SDH",
}

state = {
    "cycles": 0,
    "registered_tasks_seen": 0,
    "uins_minted": 0,
    "brns_reformatted": 0,
    "drns_reformatted": 0,
    "writebacks_ok": 0,
    "errors": 0,
    "last_cycle_at": None,
    "last_uin": None,
    "last_brn": None,
    "last_drn": None,
    "event_registrations": 0,
    "confirm_errors": 0,
    "adoptions": 0,
    "dry_run": DRY_RUN,
}


def get_db():
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return client.get_default_database()


def format_uin_sn(uin_10: str) -> str:
    u = re.sub(r"\D", "", str(uin_10))
    if len(u) != 10:
        raise ValueError(f"UIN attendu 10 digits, recu {len(u)}: {uin_10}")
    return f"SN-{u[0:4]}-{u[4:8]}-{u[8:10]}"


def region_code_for(name):
    if not name:
        return "XXX"
    key = str(name).strip().lower()
    if key in REGION_CODES:
        return REGION_CODES[key]
    for k, v in REGION_CODES.items():
        if k in key or key in k:
            return v
    return "XXX"


def _location_role(loc):
    """Retourne DISTRICT / STATE / CRVS_OFFICE d'apres identifier.value OpenCRVS."""
    for ident in loc.get("identifier", []):
        v = ident.get("value")
        if v in ("DISTRICT", "STATE", "CRVS_OFFICE"):
            return v
    return None


def get_office_region_name(db, office_ref):
    """Walk partOf : retourne le nom du Location DISTRICT (= region administrative Senegal).
    Fallback : dernier node visite juste avant un STATE (le pays)."""
    if not office_ref or "/" not in office_ref:
        return None
    loc_id = office_ref.split("/", 1)[1]
    last_name = None
    for _ in range(6):
        loc = db["Location"].find_one({"id": loc_id})
        if not loc:
            return last_name
        role = _location_role(loc)
        if role == "DISTRICT":
            return loc.get("name", "")
        if role == "STATE":
            return last_name
        last_name = loc.get("name", "")
        parent = (loc.get("partOf") or {}).get("reference", "")
        if not parent or "/" not in parent:
            return last_name
        loc_id = parent.split("/", 1)[1]
    return last_name


def get_office_from_task(task):
    for ext in task.get("extension", []):
        url = ext.get("url", "").lower()
        if url.endswith("reglastoffice") or url.endswith("reglastlocation") or "office" in url:
            ref = (ext.get("valueReference") or {}).get("reference", "")
            if ref.startswith("Location/"):
                return ref
    return None


def next_brn_sequence(db, region_code, year):
    key = f"brn:{region_code}:{year}"
    doc = db["iun_counters"].find_one_and_update(
        {"_id": key},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc.get("seq", 1))


def format_brn(region_code, year, seq):
    return f"{region_code}-{year:04d}-{seq:06d}"


def find_identifier(patient, system=None, type_code=None):
    for i, ident in enumerate(patient.get("identifier", []) or []):
        if system and ident.get("system") == system:
            return i, ident
        if type_code:
            codings = (ident.get("type") or {}).get("coding", [])
            if any(c.get("code") == type_code for c in codings):
                return i, ident
    return None, None


def extract_child_patient_id(db, composition_id):
    comp = db["Composition"].find_one({"id": composition_id})
    if not comp:
        return None
    for section in comp.get("section", []):
        codings = (section.get("code") or {}).get("coding", [])
        if any(c.get("code") == "child-details" for c in codings):
            entries = section.get("entry", [])
            if entries and entries[0].get("reference", "").startswith("Patient/"):
                return entries[0]["reference"].split("/", 1)[1]
    return None


def has_uin(patient_doc):
    idx, _ = find_identifier(patient_doc, system=UIN_SYSTEM)
    if idx is not None:
        return True
    idx, _ = find_identifier(patient_doc, system=NATIONAL_ID_SYSTEM)
    if idx is not None:
        return True
    idx, _ = find_identifier(patient_doc, type_code="NATIONAL_ID")
    if idx is not None:
        return True
    return False


async def mint_uin_raw(client):
    try:
        resp = await client.post(f"{UIN_SERVICE_URL}/v1/idgenerator/uin", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        uin = (data.get("response") or {}).get("uin") or data.get("uin")
        if uin and len(str(uin)) == 10 and str(uin).isdigit():
            return str(uin)
        log.error("reponse uin-service inattendue : %s", data)
    except Exception as exc:
        log.error("mint UIN KO : %s", exc)
    return None


async def writeback_patient(client, patient_id, uin_formatted, brn_formatted):
    try:
        r = await client.get(f"{HEARTH_URL}/fhir/Patient/{patient_id}", timeout=15)
        r.raise_for_status()
        patient = r.json()
        identifiers = patient.setdefault("identifier", [])

        idx, _ = find_identifier(patient, system=UIN_SYSTEM)
        uin_ident = {
            "system": UIN_SYSTEM,
            "type": {"coding": [{"system": "http://iun.sn/specs/identifier-type",
                                   "code": UIN_TYPE_CODE}]},
            "value": uin_formatted,
        }
        if idx is not None:
            identifiers[idx] = uin_ident
        else:
            identifiers.append(uin_ident)

        # v2.2 : ecrire aussi NATIONAL_ID (meme valeur formatee) pour {{nationalId}} du certif Handlebars
        idx, _ = find_identifier(patient, system=NATIONAL_ID_SYSTEM)
        if idx is None:
            idx, _ = find_identifier(patient, type_code="NATIONAL_ID")
        nid_ident = {
            "system": NATIONAL_ID_SYSTEM,
            "type": {"coding": [{"system": "http://opencrvs.org/specs/identifier-type",
                                   "code": "NATIONAL_ID"}]},
            "value": uin_formatted,
        }
        if idx is not None:
            identifiers[idx] = nid_ident
        else:
            identifiers.append(nid_ident)

        if brn_formatted:
            idx, _ = find_identifier(patient, system=BRN_SYSTEM)
            if idx is None:
                idx, _ = find_identifier(patient, type_code=BRN_TYPE_CODE)
            brn_ident = {
                "system": BRN_SYSTEM,
                "type": {"coding": [{"system": "http://opencrvs.org/specs/identifier-type",
                                       "code": BRN_TYPE_CODE}]},
                "value": brn_formatted,
            }
            if idx is not None:
                identifiers[idx] = brn_ident
            else:
                identifiers.append(brn_ident)

        pr = await client.put(
            f"{HEARTH_URL}/fhir/Patient/{patient_id}",
            json=patient,
            headers={"Content-Type": "application/fhir+json"},
            timeout=15,
        )
        if pr.status_code in (200, 201):
            return True
        log.error("PUT Patient %s -> HTTP %s : %s", patient_id, pr.status_code, pr.text[:300])
    except Exception as exc:
        log.error("writeback Patient %s KO : %s", patient_id, exc)
    return False


def extract_subject_patient(db, composition_id):
    """Retourne (patient_id, event_type) — cherche child-details puis deceased-details."""
    comp = db["Composition"].find_one({"id": composition_id})
    if not comp:
        return None, None
    section_map = {"child-details": EVENT_BIRTH, "deceased-details": EVENT_DEATH}
    for section in comp.get("section", []):
        codings = (section.get("code") or {}).get("coding", [])
        for c in codings:
            code = c.get("code", "")
            if code in section_map:
                entries = section.get("entry", [])
                if entries and entries[0].get("reference", "").startswith("Patient/"):
                    pid = entries[0]["reference"].split("/", 1)[1]
                    return pid, section_map[code]
    return None, None


def next_drn_sequence(db, region_code, year):
    key = f"drn:{region_code}:{year}"
    doc = db["iun_counters"].find_one_and_update(
        {"_id": key},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc.get("seq", 1))


def format_drn(region_code, year, seq):
    return f"{region_code}-{year:04d}-D{seq:06d}"


async def writeback_death(client, patient_id, uin_formatted, drn_formatted):
    """Writeback pour un Death : UIN sur deceased + DRN system death-registration-number."""
    try:
        r = await client.get(f"{HEARTH_URL}/fhir/Patient/{patient_id}", timeout=15)
        r.raise_for_status()
        patient = r.json()
        identifiers = patient.setdefault("identifier", [])

        # UIN
        idx, _ = find_identifier(patient, system=UIN_SYSTEM)
        uin_ident = {
            "system": UIN_SYSTEM,
            "type": {"coding": [{"system": "http://iun.sn/specs/identifier-type",
                                   "code": UIN_TYPE_CODE}]},
            "value": uin_formatted,
        }
        if idx is not None:
            identifiers[idx] = uin_ident
        else:
            identifiers.append(uin_ident)

        # national-id (same value, pour Handlebars compat)
        idx, _ = find_identifier(patient, system=NATIONAL_ID_SYSTEM)
        nid_ident = {
            "system": NATIONAL_ID_SYSTEM,
            "type": {"coding": [{"system": "http://opencrvs.org/specs/identifier-type",
                                   "code": "NATIONAL_ID"}]},
            "value": uin_formatted,
        }
        if idx is not None:
            identifiers[idx] = nid_ident
        else:
            identifiers.append(nid_ident)

        # DRN (replace le DRN OpenCRVS opaque)
        if drn_formatted:
            idx, _ = find_identifier(patient, system=DRN_SYSTEM)
            if idx is None:
                idx, _ = find_identifier(patient, type_code=DRN_TYPE_CODE)
            drn_ident = {
                "system": DRN_SYSTEM,
                "type": {"coding": [{"system": "http://opencrvs.org/specs/identifier-type",
                                       "code": DRN_TYPE_CODE}]},
                "value": drn_formatted,
            }
            if idx is not None:
                identifiers[idx] = drn_ident
            else:
                identifiers.append(drn_ident)

        pr = await client.put(
            f"{HEARTH_URL}/fhir/Patient/{patient_id}",
            json=patient,
            headers={"Content-Type": "application/fhir+json"},
            timeout=15,
        )
        if pr.status_code in (200, 201):
            return True
        log.error("PUT Death Patient %s HTTP %s : %s", patient_id, pr.status_code, pr.text[:300])
    except Exception as exc:
        log.error("writeback Death Patient %s KO : %s", patient_id, exc)
    return False


async def run_cycle():
    db = get_db()
    async with httpx.AsyncClient() as client:
        tasks = list(
            db["Task"]
            .find({"businessStatus.coding.code": REG_STATUS_REGISTERED})
            .sort("meta.lastUpdated", -1)
            .limit(100)
        )
        state["registered_tasks_seen"] = len(tasks)
        if not tasks:
            log.info("cycle : aucune Task REGISTERED")
            return
        log.info("cycle : %d Task(s) REGISTERED", len(tasks))
        for task in tasks:
            focus_ref = (task.get("focus") or {}).get("reference", "")
            if not focus_ref.startswith("Composition/"):
                continue
            comp_id = focus_ref.split("/", 1)[1]
            patient_id, event_type = extract_subject_patient(db, comp_id)
            if not patient_id or not event_type:
                log.warning("Composition %s : sujet ni birth ni death detecte", comp_id)
                continue
            patient = db["Patient"].find_one({"id": patient_id})
            if not patient:
                log.warning("Patient %s absent de Mongo", patient_id)
                continue
            if has_uin(patient):
                continue

            # v3.0 : adoption — UIN deja mint par /event-registration (identifiant configurable),
            # on le copie vers UIN_SYSTEM + national-id pour /records et le certificat bridge.
            _, bcid = find_identifier(patient, type_code=BCID1_TYPE_CODE)
            if bcid is None:
                _, bcid = find_identifier(patient, system=BCID1_SYSTEM)
            if bcid is not None and bcid.get("value"):
                adopt_uin = bcid["value"]
                _, brn_id = find_identifier(patient, system=BRN_SYSTEM)
                if brn_id is None:
                    _, brn_id = find_identifier(patient, type_code=BRN_TYPE_CODE)
                adopt_brn = brn_id.get("value") if brn_id else None
                if DRY_RUN:
                    log.info("DRY_RUN adoption : Patient/%s UIN=%s", patient_id, adopt_uin)
                    continue
                ok = await writeback_patient(client, patient_id, adopt_uin, adopt_brn)
                if ok:
                    state["adoptions"] += 1
                    log.info("adoption v3 : UIN %s -> UIN_SYSTEM/national-id pour Patient/%s", adopt_uin, patient_id)
                else:
                    state["errors"] += 1
                continue

            office_ref = get_office_from_task(task)
            region_name = get_office_region_name(db, office_ref) if office_ref else None
            region_code = region_code_for(region_name)
            year = datetime.now(timezone.utc).year

            name = ""
            try:
                n0 = (patient.get("name") or [{}])[0]
                name = " ".join([p.strip() for p in n0.get('given', []) if p and p.strip()] + [n0.get('family', '').strip()]).strip()
            except Exception:
                pass

            if DRY_RUN:
                log.info("DRY_RUN : %s Patient/%s (%s) region=%s office=%s",
                         event_type, patient_id, name or "?", region_code, office_ref or "?")
                continue

            uin_raw = await mint_uin_raw(client)
            if not uin_raw:
                state["errors"] += 1
                continue
            try:
                uin_fmt = format_uin_sn(uin_raw)
            except ValueError as e:
                log.error("format UIN KO : %s", e)
                state["errors"] += 1
                continue

            if event_type == EVENT_BIRTH:
                brn_fmt = None
                if region_code != "XXX":
                    seq = next_brn_sequence(db, region_code, year)
                    brn_fmt = format_brn(region_code, year, seq)
                    state["brns_reformatted"] += 1
                    state["last_brn"] = brn_fmt
                else:
                    log.warning("Patient/%s BIRTH : region inconnue, BRN skip", patient_id)
                state["uins_minted"] += 1
                state["last_uin"] = uin_fmt
                log.info("BIRTH UIN %s + BRN %s pour Patient/%s (%s)",
                         uin_fmt, brn_fmt or "-", patient_id, name or "?")
                ok = await writeback_patient(client, patient_id, uin_fmt, brn_fmt)
            elif event_type == EVENT_DEATH:
                drn_fmt = None
                if region_code != "XXX":
                    seq = next_drn_sequence(db, region_code, year)
                    drn_fmt = format_drn(region_code, year, seq)
                    state["drns_reformatted"] += 1
                    state["last_drn"] = drn_fmt
                else:
                    log.warning("Patient/%s DEATH : region inconnue, DRN skip", patient_id)
                state["uins_minted"] += 1
                state["last_uin"] = uin_fmt
                log.info("DEATH UIN %s + DRN %s pour Patient/%s (%s)",
                         uin_fmt, drn_fmt or "-", patient_id, name or "?")
                ok = await writeback_death(client, patient_id, uin_fmt, drn_fmt)
            else:
                log.warning("event type inconnu : %s", event_type)
                continue

            if ok:
                state["writebacks_ok"] += 1
                log.info("identifiers ecrits dans Hearth pour Patient/%s (%s)", patient_id, event_type)
            else:
                state["errors"] += 1


async def poller():
    while True:
        try:
            await run_cycle()
        except Exception as exc:
            state["errors"] += 1
            log.error("cycle KO : %s", exc)
        state["cycles"] += 1
        state["last_cycle_at"] = datetime.now(timezone.utc).isoformat()
        await asyncio.sleep(POLL_INTERVAL_S)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log.info("iun-uin-bridge v3.0 demarre (DRY_RUN=%s, poll=%ss, uin=%s)",
             DRY_RUN, POLL_INTERVAL_S, UIN_SERVICE_URL)
    task = asyncio.create_task(poller())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)



def _boom_500(msg):
    """Reponse d'erreur au format boom (hapi) : msg devient la raison de rejet cote core,
    et l'enregistrement est RETENTE par opencrvs-core via countryconfig."""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={
        "statusCode": 500,
        "error": "Internal Server Error",
        "message": "An internal server error occurred",
        "msg": str(msg),
    })


async def confirm_registration(client, composition_id, registration_number, identifiers, auth_header):
    """Mutation confirmRegistration du gateway (contrat identique au handler countryconfig)."""
    query = (
        "mutation confirmRegistration($id: ID!, $details: ConfirmRegistrationInput!) "
        "{ confirmRegistration(id: $id, details: $details) }"
    )
    details = {"registrationNumber": registration_number}
    if identifiers:
        details["identifiers"] = identifiers
    headers = {"Content-Type": "application/json"}
    if auth_header:
        headers["Authorization"] = auth_header
    try:
        r = await client.post(
            f"{GATEWAY_URL}/graphql",
            json={"query": query, "variables": {"id": composition_id, "details": details}},
            headers=headers,
            timeout=30,
        )
        if r.status_code != 200:
            return False, f"HTTP {r.status_code} : {r.text[:300]}"
        data = r.json()
        if data.get("errors"):
            return False, str(data["errors"])[:300]
        return True, None
    except Exception as exc:
        return False, str(exc)


@app.post("/event-registration")
async def event_registration(request: Request):
    state["event_registrations"] += 1
    # --- parse du bundle FHIR envoye par workflow ---
    try:
        bundle = await request.json()
        entries = [e.get("resource", {}) for e in (bundle.get("entry") or [])]
        task = next((r for r in entries if r.get("resourceType") == "Task"), None)
        comp = next((r for r in entries if r.get("resourceType") == "Composition"), None)
        if task is None or comp is None or not comp.get("id"):
            raise ValueError("Task ou Composition absente du bundle")
        composition_id = comp["id"]
        event_code = ""
        codings = (task.get("code") or {}).get("coding", [])
        if codings:
            event_code = codings[0].get("code", "")
        tracking_id = None
        for ident in task.get("identifier", []) or []:
            if str(ident.get("system", "")).endswith("-tracking-id"):
                tracking_id = ident.get("value")
                break
        if not tracking_id:
            raise ValueError("tracking id introuvable dans la Task")
    except Exception as exc:
        log.error("/event-registration : bundle invalide : %s", exc)
        return _boom_500(f"bridge IUN : bundle invalide : {exc}")

    auth = request.headers.get("authorization", "")
    db = get_db()
    year = datetime.now(timezone.utc).year
    fallback_regno = f"{year}{tracking_id}"

    # --- idempotence retry : si deja mint pour cette composition, reutiliser ---
    prev = db["iun_event_reg"].find_one({"_id": composition_id})

    office_ref = get_office_from_task(task)
    region_name = get_office_region_name(db, office_ref) if office_ref else None
    region_code = region_code_for(region_name)

    async with httpx.AsyncClient() as client:
        regno = fallback_regno
        identifiers = None
        uin_fmt = None
        if prev:
            regno = prev.get("regno") or fallback_regno
            uin_fmt = prev.get("uin")
            if uin_fmt:
                identifiers = [{"type": BCID1_TYPE_CODE, "value": uin_fmt}]
            log.info("/event-registration retry : reutilise regno=%s uin=%s (%s)",
                     regno, uin_fmt or "-", composition_id)
        elif DRY_RUN:
            log.info("/event-registration DRY_RUN : passthrough regno=%s (%s)", regno, event_code)
        else:
            if event_code == "BIRTH":
                uin_raw = await mint_uin_raw(client)
                if not uin_raw:
                    state["confirm_errors"] += 1
                    return _boom_500("bridge IUN : mint UIN impossible (iun-uin-service injoignable)")
                try:
                    uin_fmt = format_uin_sn(uin_raw)
                except ValueError as e:
                    return _boom_500(f"bridge IUN : format UIN KO : {e}")
                identifiers = [{"type": BCID1_TYPE_CODE, "value": uin_fmt}]
                if region_code != "XXX":
                    seq = next_brn_sequence(db, region_code, year)
                    regno = format_brn(region_code, year, seq)
                    state["last_brn"] = regno
                else:
                    log.warning("/event-registration BIRTH : region inconnue (office=%s), regno fallback", office_ref)
                state["uins_minted"] += 1
                state["last_uin"] = uin_fmt
            elif event_code == "DEATH":
                if region_code != "XXX":
                    seq = next_drn_sequence(db, region_code, year)
                    regno = format_drn(region_code, year, seq)
                    state["last_drn"] = regno
                # l'UIN du defunt reste gere par le reconciliateur (writeback direct Hearth)
            else:
                log.warning("/event-registration : event %s inconnu, regno fallback", event_code or "?")
            db["iun_event_reg"].replace_one(
                {"_id": composition_id},
                {"_id": composition_id, "regno": regno, "uin": uin_fmt,
                 "event": event_code, "tracking_id": tracking_id,
                 "ts": datetime.now(timezone.utc).isoformat()},
                upsert=True,
            )

        ok, err = await confirm_registration(client, composition_id, regno, identifiers, auth)

    if not ok:
        state["confirm_errors"] += 1
        log.error("/event-registration : confirmRegistration KO : %s", err)
        return _boom_500(f"bridge IUN : confirmRegistration KO : {err}")

    log.info("/event-registration OK : %s comp=%s regno=%s uin=%s",
             event_code, composition_id, regno, uin_fmt or "-")
    from fastapi.responses import Response
    return Response(status_code=202)


@app.get("/ping")
def ping():
    return {"success": True}


@app.get("/status")
def status():
    return state


@app.get("/format-preview")
def format_preview(uin: str = "1234567890", region: str = "dakar"):
    try:
        uin_out = format_uin_sn(uin)
    except ValueError as e:
        uin_out = f"error: {e}"
    rc = region_code_for(region)
    return {
        "uin_input": uin,
        "uin_formatted": uin_out,
        "region_input": region,
        "region_code": rc,
        "brn_example": format_brn(rc, datetime.now(timezone.utc).year, 42),
    }


@app.get("/office-region")
def office_region(office_ref: str = "Location/d60e05d4-7781-467c-b4c1-13fd89346845"):
    """Debug : verifier le mapping office -> region (name puis code)."""
    db = get_db()
    name = get_office_region_name(db, office_ref)
    return {"office_ref": office_ref, "region_name": name, "region_code": region_code_for(name)}


# --- v2.3 : endpoint standalone de rendu SVG cert ---
try:
    with open(os.path.join(os.path.dirname(__file__), "birth-certificate.svg"), "r", encoding="utf-8") as _f:
        SVG_TEMPLATE = _f.read()
    log.info("SVG template charge (%d chars)", len(SVG_TEMPLATE))
except Exception as _e:
    log.warning("SVG template non trouve : %s (le endpoint /certificate/birth renverra 500)", _e)
    SVG_TEMPLATE = None


def _find_related_patient(db, composition_id, section_code):
    """Fouille la Composition pour trouver le Patient de la section child/mother/father."""
    comp = db["Composition"].find_one({"id": composition_id})
    if not comp:
        return None
    for section in comp.get("section", []):
        codings = (section.get("code") or {}).get("coding", [])
        if any(c.get("code") == section_code for c in codings):
            entries = section.get("entry", [])
            if entries and entries[0].get("reference", "").startswith("Patient/"):
                pid = entries[0]["reference"].split("/", 1)[1]
                return db["Patient"].find_one({"id": pid})
    return None


def _find_task_for_composition(db, composition_id):
    return db["Task"].find_one({"focus.reference": f"Composition/{composition_id}"})


def _extract_tracking_id(task):
    """OpenCRVS store le tracking ID en Task.identifier system birth-tracking-id."""
    if not task:
        return ""
    for ident in task.get("identifier", []):
        s = str(ident.get("system", ""))
        if s.endswith("/birth-tracking-id") or s.endswith("/death-tracking-id"):
            return str(ident.get("value", ""))
    return ""


def _find_informant(db, composition_id):
    """Section informant-details -> RelatedPerson -> patient link."""
    comp = db["Composition"].find_one({"id": composition_id})
    if not comp:
        return None
    for section in comp.get("section", []):
        codings = (section.get("code") or {}).get("coding", [])
        if any(c.get("code") == "informant-details" for c in codings):
            entries = section.get("entry", [])
            if entries:
                ref = entries[0].get("reference", "")
                if ref.startswith("RelatedPerson/"):
                    rp_id = ref.split("/", 1)[1]
                    rp = db["RelatedPerson"].find_one({"id": rp_id})
                    if rp and (rp.get("patient") or {}).get("reference", "").startswith("Patient/"):
                        pid = rp["patient"]["reference"].split("/", 1)[1]
                        return db["Patient"].find_one({"id": pid})
                elif ref.startswith("Patient/"):
                    pid = ref.split("/", 1)[1]
                    return db["Patient"].find_one({"id": pid})
    return None


def _make_qr_data_uri(verify_url: str) -> str:
    """Genere un QR code SVG-inline via segno et retourne un data URI PNG base64."""
    try:
        qr = segno.make(verify_url, error='m')
        buf = io.BytesIO()
        qr.save(buf, kind='png', scale=8, border=2)
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        log.warning("QR gen KO : %s", e)
        return ""


def _patient_display_name(patient):
    if not patient:
        return "", ""
    try:
        n = (patient.get("name") or [{}])[0]
        given = " ".join(p.strip() for p in n.get("given", []) if p and p.strip())
        family = n.get("family", "")
        return given, family
    except Exception:
        return "", ""


def _find_composition_for_patient(db, patient_id):
    """Trouve la Composition qui contient ce Patient comme child."""
    for comp in db["Composition"].find({"section.entry.reference": f"Patient/{patient_id}"}):
        return comp
    return None


@app.get("/certificate/birth/{patient_id}")
async def render_birth_certificate(patient_id: str):
    """Rend le cert de naissance SVG rempli avec les donnees FHIR de ce Patient enfant."""
    from fastapi.responses import Response, PlainTextResponse
    if SVG_TEMPLATE is None:
        return PlainTextResponse("SVG template absent du deployment", status_code=500)
    db = get_db()
    child = db["Patient"].find_one({"id": patient_id})
    if not child:
        return PlainTextResponse(f"Patient {patient_id} not found in Hearth", status_code=404)

    child_given, child_family = _patient_display_name(child)
    child_gender = {"male": "Masculin", "female": "Féminin"}.get(str(child.get("gender", "")), str(child.get("gender", "")))
    child_birth = str(child.get("birthDate", ""))

    uin = brn = ""
    for ident in child.get("identifier", []):
        s = ident.get("system", "")
        v = str(ident.get("value", ""))
        if s == UIN_SYSTEM: uin = v
        elif s == BRN_SYSTEM: brn = v

    comp = _find_composition_for_patient(db, patient_id)
    mother_given = mother_family = ""
    father_given = father_family = ""
    informant_given = informant_family = ""
    place_of_birth = ""
    reg_office_name = "Bureau Etat Civil Dakar Plateau"
    reg_date = ""
    tracking_id = ""
    if comp:
        mother = _find_related_patient(db, comp["id"], "mother-details")
        father = _find_related_patient(db, comp["id"], "father-details")
        informant = _find_informant(db, comp["id"])
        mother_given, mother_family = _patient_display_name(mother)
        father_given, father_family = _patient_display_name(father)
        informant_given, informant_family = _patient_display_name(informant)
        reg_date = str((comp.get("date") or ""))[:10]
        task = _find_task_for_composition(db, comp["id"])
        tracking_id = _extract_tracking_id(task)

    # lieu de naissance : Encounter location > Patient.address
    if comp:
        for section in comp.get("section", []):
            codings = (section.get("code") or {}).get("coding", [])
            if any(c.get("code") == "birth-encounter" for c in codings):
                entries = section.get("entry", [])
                if entries and entries[0].get("reference", "").startswith("Encounter/"):
                    enc = db["Encounter"].find_one({"id": entries[0]["reference"].split("/", 1)[1]})
                    if enc:
                        for loc in enc.get("location", []):
                            ref = (loc.get("location") or {}).get("reference", "")
                            if ref.startswith("Location/"):
                                lid = ref.split("/", 1)[1]
                                lresource = db["Location"].find_one({"id": lid})
                                if lresource:
                                    place_of_birth = lresource.get("name", "")
                                    break
    if not place_of_birth:
        try:
            addr = (child.get("address") or [{}])[0]
            place_of_birth = addr.get("city") or addr.get("district") or addr.get("country") or ""
        except Exception:
            pass

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # QR code de verification pointe vers l'URL cert de ce patient
    verify_url = f"https://iun-uin-cert-dev.apps.origins.heritage.africa/certificate/birth/{patient_id}"
    qr_uri = _make_qr_data_uri(verify_url)

    informant_name = f"{informant_given} {informant_family}".strip() or "-"

    replacements = {
        "childFirstName": child_given,
        "childFamilyName": child_family,
        "childGender": child_gender or "-",
        "eventDate": child_birth or "-",
        "placeOfBirth": place_of_birth or reg_office_name,
        "registrationNumber": brn or "-",
        "nationalId": uin or "-",
        "motherFirstName": mother_given,
        "motherFamilyName": mother_family or "-",
        "fatherFirstName": father_given,
        "fatherFamilyName": father_family or "-",
        "registrationLocation": reg_office_name,
        "registrationDate": reg_date or now_iso,
        "certificateDate": now_iso,
        "registrarName": "Officier d'Etat Civil",
        "qrCodeDataUri": qr_uri,
        "trackingId": tracking_id or "-",
        "informantName": informant_name,
    }
    svg = SVG_TEMPLATE
    for k, v in replacements.items():
        svg = svg.replace("{{" + k + "}}", str(v))
    return Response(content=svg, media_type="image/svg+xml")


# --- v2.4 : /records liste HTML ---

_RECORDS_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>République du Sénégal — Registre des actes de naissance</title>
  <style>
    :root {
      --vert: #14522F; --vert-fonce: #0E3B22; --vert-header: #123D25;
      --or: #C9A227; --or-sombre: #8A7430; --or-pale: #E4D5A2;
      --creme: #F6F1DE; --creme-2: #EFE8CF; --blanc: #FFFDF6;
      --encre: #26211A; --encre-2: #5C5442; --filet: #DACFA8;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--creme); color: var(--encre);
           font-family: Georgia, "Times New Roman", "Libre Baskerville", serif; }
    header.bandeau { background: var(--vert-header); color: #F3EDD8; }
    .bandeau-inner { max-width: 1180px; margin: 0 auto; padding: 12px 32px;
      display: flex; align-items: center; gap: 16px; }
    .drapeau { width: 30px; height: 20px; flex: none; border: 1px solid rgba(255,255,255,.35); }
    .bandeau .rep { font-variant: small-caps; letter-spacing: 3px; font-size: 17px; font-weight: 600; }
    .bandeau .devise { font-style: italic; font-size: 14px; color: #D8CFA9; margin-left: 10px; }
    .masthead { background: var(--creme); border-bottom: 1px solid var(--filet); }
    .mast-inner { max-width: 1180px; margin: 0 auto; padding: 30px 32px 22px;
      display: flex; align-items: center; justify-content: space-between; gap: 24px; }
    .mast-inner h1 { margin: 0 0 6px; font-size: 38px; font-weight: 600; color: var(--vert-fonce); letter-spacing: 0.5px; }
    .mast-inner .sous { margin: 0; font-size: 16.5px; font-style: italic; color: var(--encre-2); }
    .sur-titre { font-variant: small-caps; letter-spacing: 3.5px; font-size: 15px; color: var(--or-sombre); margin-bottom: 4px; }
    .emblem { flex: none; }
    .filet-or { max-width: 1180px; margin: 0 auto; padding: 0 32px; }
    .filet-or hr { border: none; border-top: 1px dashed var(--or); margin: 0; }
    main { max-width: 1180px; margin: 0 auto; padding: 26px 32px 44px; }
    .barre-outils { display: flex; align-items: end; justify-content: space-between; gap: 20px; flex-wrap: wrap; margin-bottom: 20px; }
    .stats { display: flex; gap: 16px; }
    .stat { background: var(--blanc); border: 1px solid var(--filet);
            padding: 12px 20px 12px; min-width: 165px; }
    .stat .num { font-variant: small-caps; letter-spacing: 3px; font-size: 12.5px; color: var(--or-sombre); }
    .stat .val { font-size: 32px; font-weight: 600; color: var(--vert-fonce); line-height: 1.15; }
    .stat .lbl { font-variant: small-caps; letter-spacing: 1.5px; font-size: 14px; color: var(--encre-2); }
    .recherche { position: relative; }
    .recherche input { width: 360px; max-width: 72vw; padding: 12px 14px 12px 38px;
      border: 1px solid var(--filet); font-size: 16px;
      background: var(--blanc); color: var(--encre); font-family: inherit; }
    .recherche input:focus { outline: 2px solid var(--vert); outline-offset: -1px; }
    .recherche svg { position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
      width: 15px; height: 15px; stroke: var(--or-sombre); fill: none; stroke-width: 2; }
    .cadre { background: var(--blanc); border: 1px solid var(--filet); }
    .cadre-titre { padding: 16px 20px 13px; border-bottom: 2px solid var(--vert);
      display: flex; justify-content: space-between; align-items: baseline; }
    .cadre-titre h2 { margin: 0; font-variant: small-caps; font-size: 21px; font-weight: 650;
      color: var(--vert-fonce); letter-spacing: 2px; }
    .cadre-titre .maj { font-size: 14px; font-style: italic; color: var(--encre-2); }
    table { width: 100%; border-collapse: collapse; }
    thead th { text-align: left; padding: 12px 20px; font-size: 13.5px; letter-spacing: 2px;
      font-variant: small-caps; color: var(--or-sombre); font-weight: 600;
      border-bottom: 1px solid var(--filet); background: var(--creme-2); }
    td { padding: 15px 20px; font-size: 16.5px; border-bottom: 1px solid var(--creme-2); }
    tbody tr:last-child td { border-bottom: none; }
    tbody tr:hover { background: var(--creme); }
    td.brn, td.uin { font-family: Consolas, "SF Mono", Menlo, monospace;
      font-variant-numeric: tabular-nums; font-size: 15px; }
    td.brn { color: var(--encre); font-weight: 600; }
    td.uin { color: var(--vert); font-weight: 700; }
    a.btn { display: inline-block; padding: 9px 18px; background: var(--vert);
      color: #F5EFDC; text-decoration: none; font-variant: small-caps; letter-spacing: 1.8px;
      font-size: 14.5px; font-weight: 600; border: 1px solid var(--vert-fonce); }
    a.btn:hover { background: var(--vert-fonce); }
    .empty { text-align: center; padding: 48px 20px 56px; color: var(--encre-2); font-size: 16.5px; font-style: italic; }
    .empty svg { display: block; margin: 0 auto 10px; }
    footer { border-top: 1px dashed var(--or); background: var(--creme); margin-top: 44px; }
    .foot-inner { max-width: 1180px; margin: 0 auto; padding: 16px 32px;
      display: flex; justify-content: space-between; font-size: 14px; font-style: italic; color: var(--encre-2); }
    @media print { .recherche, a.btn { display: none; } }
  </style>
</head>
<body>
  <header class="bandeau">
    <div class="bandeau-inner">
      <svg class="drapeau" viewBox="0 0 30 20" aria-hidden="true">
        <rect width="10" height="20" fill="#00853F"/><rect x="10" width="10" height="20" fill="#FDEF42"/><rect x="20" width="10" height="20" fill="#E31B23"/>
        <path d="M15 6.2 16.05 9.1h3.05l-2.45 1.85.9 3-2.55-1.8-2.55 1.8.9-3-2.45-1.85h3.05Z" fill="#00853F"/>
      </svg>
      <span class="rep">République du Sénégal</span>
      <span class="devise">Un Peuple · Un But · Une Foi</span>
    </div>
  </header>
  <div class="masthead">
    <div class="mast-inner">
      <div class="marque">
        <div class="sur-titre">Ministère de l'Intérieur et de la Sécurité Publique</div>
        <h1>Registre des actes de naissance</h1>
        <p class="sous">Direction Générale de l'État Civil — Système national d'identification (IUN)</p>
      </div>
      <div class="emblem"><svg viewBox="55 75 495 520" width="185" height="196" aria-hidden="true"><path d="M 200 560 C 210 500, 220 450, 240 400 C 254 362, 262 330, 264 302 L 336 302 C 338 330, 346 362, 360 400 C 380 450, 390 500, 400 560 C 370 545, 230 545, 200 560 Z" fill="#DFE3CA"/>
<path d="M 200 560 C 185 566, 160 572, 138 584 C 175 570, 205 566, 226 562 Z" fill="#DFE3CA"/>
<path d="M 400 560 C 415 566, 440 572, 462 584 C 425 570, 395 566, 374 562 Z" fill="#DFE3CA"/>
<path d="M 262 556 C 258 566, 250 576, 240 586 C 254 576, 262 568, 268 558 Z" fill="#DFE3CA"/>
<path d="M 338 556 C 342 566, 350 576, 360 586 C 346 576, 338 568, 332 558 Z" fill="#DFE3CA"/><path d="M 266 310 L 183 292" stroke="#DFE3CA" stroke-width="19" fill="none" stroke-linecap="round"/><path d="M 183 292 L 138 260" stroke="#DFE3CA" stroke-width="9.9" fill="none" stroke-linecap="round"/><path d="M 138 260 L 102 249" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 102 249 L 87 232" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 87 232 L 86 220" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 87 232 L 82 220" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 102 249 L 86 229" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 86 229 L 70 222" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 86 229 L 82 212" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 138 260 L 115 235" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 115 235 L 108 217" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 108 217 L 97 209" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 108 217 L 110 204" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 115 235 L 109 214" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 109 214 L 103 203" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 109 214 L 112 202" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 183 292 L 124 296" stroke="#DFE3CA" stroke-width="9.9" fill="none" stroke-linecap="round"/><path d="M 124 296 L 99 275" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 99 275 L 77 270" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 77 270 L 64 271" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 77 270 L 65 261" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 99 275 L 80 263" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 80 263 L 72 251" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 80 263 L 71 254" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 124 296 L 94 273" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 94 273 L 74 260" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 74 260 L 71 246" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 74 260 L 69 247" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 94 273 L 78 253" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 78 253 L 81 238" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 78 253 L 66 245" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 276 303 L 219 255" stroke="#DFE3CA" stroke-width="19" fill="none" stroke-linecap="round"/><path d="M 219 255 L 197 211" stroke="#DFE3CA" stroke-width="9.9" fill="none" stroke-linecap="round"/><path d="M 197 211 L 172 197" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 172 197 L 155 191" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 155 191 L 148 183" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 155 191 L 150 180" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 172 197 L 156 187" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 156 187 L 149 179" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 156 187 L 145 187" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 197 211 L 190 182" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 190 182 L 174 168" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 174 168 L 173 155" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 174 168 L 164 161" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 190 182 L 187 164" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 187 164 L 187 151" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 187 164 L 184 151" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 219 255 L 176 254" stroke="#DFE3CA" stroke-width="9.9" fill="none" stroke-linecap="round"/><path d="M 176 254 L 154 238" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 154 238 L 138 236" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 138 236 L 127 231" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 138 236 L 128 237" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 154 238 L 142 226" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 142 226 L 131 224" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 142 226 L 131 227" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 176 254 L 159 235" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 159 235 L 151 220" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 151 220 L 151 208" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 151 220 L 147 211" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 159 235 L 151 222" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 151 222 L 151 212" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 151 222 L 145 215" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 300 298 L 300 232" stroke="#DFE3CA" stroke-width="19" fill="none" stroke-linecap="round"/><path d="M 300 232 L 320 193" stroke="#DFE3CA" stroke-width="9.9" fill="none" stroke-linecap="round"/><path d="M 320 193 L 327 167" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 327 167 L 319 155" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 319 155 L 311 151" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 319 155 L 310 152" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 327 167 L 341 159" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 341 159 L 347 151" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 341 159 L 345 149" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 320 193 L 318 165" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 318 165 L 310 148" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 310 148 L 304 139" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 310 148 L 302 139" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 318 165 L 308 150" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 308 150 L 301 142" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 308 150 L 298 143" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 300 232 L 306 188" stroke="#DFE3CA" stroke-width="9.9" fill="none" stroke-linecap="round"/><path d="M 306 188 L 296 159" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 296 159 L 292 141" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 292 141 L 287 129" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 292 141 L 293 127" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 296 159 L 280 152" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 280 152 L 276 143" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 280 152 L 277 141" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 306 188 L 303 159" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 303 159 L 294 142" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 294 142 L 284 136" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 294 142 L 285 135" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 303 159 L 307 141" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 307 141 L 309 129" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 307 141 L 317 133" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 324 303 L 381 255" stroke="#DFE3CA" stroke-width="19" fill="none" stroke-linecap="round"/><path d="M 381 255 L 421 240" stroke="#DFE3CA" stroke-width="9.9" fill="none" stroke-linecap="round"/><path d="M 421 240 L 444 233" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 444 233 L 457 224" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 457 224 L 466 220" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 457 224 L 459 216" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 444 233 L 458 231" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 458 231 L 466 232" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 458 231 L 467 232" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 421 240 L 445 223" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 445 223 L 454 207" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 454 207 L 454 195" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 454 207 L 466 203" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 445 223 L 464 215" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 464 215 L 474 208" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 464 215 L 473 206" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 381 255 L 426 243" stroke="#DFE3CA" stroke-width="9.9" fill="none" stroke-linecap="round"/><path d="M 426 243 L 457 235" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 457 235 L 476 236" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 476 236 L 487 232" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 476 236 L 486 236" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 457 235 L 474 233" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 474 233 L 484 228" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 474 233 L 486 234" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 426 243 L 442 219" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 442 219 L 450 205" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 450 205 L 451 195" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 450 205 L 450 195" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 442 219 L 453 202" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 453 202 L 465 197" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 453 202 L 456 188" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 334 310 L 417 292" stroke="#DFE3CA" stroke-width="19" fill="none" stroke-linecap="round"/><path d="M 417 292 L 465 285" stroke="#DFE3CA" stroke-width="9.9" fill="none" stroke-linecap="round"/><path d="M 465 285 L 488 271" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 488 271 L 501 263" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 501 263 L 508 257" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 501 263 L 508 254" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 488 271 L 504 272" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 504 272 L 514 273" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 504 272 L 513 273" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 465 285 L 488 270" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 488 270 L 502 262" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 502 262 L 509 255" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 502 262 L 512 261" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 488 270 L 500 259" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 500 259 L 510 256" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 500 259 L 508 253" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 417 292 L 469 265" stroke="#DFE3CA" stroke-width="9.9" fill="none" stroke-linecap="round"/><path d="M 469 265 L 505 267" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 505 267 L 525 255" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 525 255 L 533 243" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 525 255 L 539 250" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 505 267 L 522 254" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 522 254 L 530 245" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 522 254 L 532 247" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 469 265 L 509 259" stroke="#DFE3CA" stroke-width="5.1" fill="none" stroke-linecap="round"/><path d="M 509 259 L 535 256" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 535 256 L 550 256" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 535 256 L 548 250" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 509 259 L 530 242" stroke="#DFE3CA" stroke-width="2.7" fill="none" stroke-linecap="round"/><path d="M 530 242 L 537 228" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/><path d="M 530 242 L 547 241" stroke="#DFE3CA" stroke-width="1.4" fill="none" stroke-linecap="round"/></svg></div>
    </div>
  </div>
  <div class="filet-or"><hr></div>
  <main>
    <div class="barre-outils">
      <div class="stats">
        <div class="stat"><div class="num">I</div><div class="val">{{totalRecords}}</div><div class="lbl">Actes enregistrés</div></div>
        <div class="stat"><div class="num">II</div><div class="val">{{regionsUsed}}</div><div class="lbl">Régions couvertes</div></div>
        <div class="stat"><div class="num">III</div><div class="val">{{today}}</div><div class="lbl">Généré le</div></div>
      </div>
      <div class="recherche">
        <svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/></svg>
        <input id="q" type="search" placeholder="Rechercher par nom, BRN ou IUN…" aria-label="Rechercher">
      </div>
    </div>
    <div class="cadre">
      <div class="cadre-titre">
        <h2>Extraits délivrables</h2>
        <div class="maj">Dernière mise à jour : {{now}}</div>
      </div>
      __TABLE__
    </div>
  </main>
  <footer>
    <div class="foot-inner">
      <div>République du Sénégal — Ministère de l'Intérieur et de la Sécurité Publique</div>
      <div>IUN Sénégal · __VERSION__ · {{now}}</div>
    </div>
  </footer>
  <script>
    var q = document.getElementById('q');
    if (q) q.addEventListener('input', function () {
      var v = this.value.trim().toLowerCase();
      document.querySelectorAll('tbody tr').forEach(function (tr) {
        tr.style.display = tr.textContent.toLowerCase().indexOf(v) === -1 ? 'none' : '';
      });
    });
  </script>
</body>
</html>"""


@app.get("/records")
def records_list():
    """Page HTML listant tous les Patients qui ont un IUN (registre d'actes de naissance)."""
    from fastapi.responses import HTMLResponse
    db = get_db()
    pats = list(
        db["Patient"]
        .find({"identifier.system": UIN_SYSTEM})
        .sort("meta.lastUpdated", -1)
        .limit(200)
    )
    rows = []
    regions = set()
    for p in pats:
        given, family = _patient_display_name(p)
        full = f"{given} {family}".strip() or "?"
        uin = brn = ""
        for ident in p.get("identifier", []):
            s = ident.get("system", "")
            v = str(ident.get("value", ""))
            if s == UIN_SYSTEM: uin = v
            elif s == BRN_SYSTEM: brn = v
        if brn and "-" in brn:
            regions.add(brn.split("-", 1)[0])
        pid = p.get("id", "")
        cert_url = f"/certificate/birth/{pid}"
        birth_date = str(p.get("birthDate", ""))
        gender = {"male": "Masculin", "female": "Féminin"}.get(str(p.get("gender", "")), str(p.get("gender", "")))
        rows.append(
            f'<tr>'
            f'<td>{full}</td>'
            f'<td>{gender}</td>'
            f'<td>{birth_date}</td>'
            f'<td class="brn">{brn or "-"}</td>'
            f'<td class="uin">{uin or "-"}</td>'
            f'<td><a class="btn" href="{cert_url}" target="_blank">Voir cert</a></td>'
            f'</tr>'
        )

    if rows:
        table_html = (
            '<table>'
            '<thead><tr>'
            '<th>Nom complet</th>'
            '<th>Sexe</th>'
            '<th>Date naissance</th>'
            '<th>BRN</th>'
            '<th>IUN</th>'
            '<th>Action</th>'
            '</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody>'
            '</table>'
        )
    else:
        table_html = '<div class="empty">Aucun acte enregistre pour le moment</div>'

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    html = (_RECORDS_HTML_TEMPLATE
        .replace("__TABLE__", table_html)
        .replace("{{totalRecords}}", str(len(pats)))
        .replace("{{regionsUsed}}", str(len(regions)))
        .replace("{{today}}", today)
        .replace("{{now}}", now)
        .replace("__VERSION__", "v3.0")
    )
    return HTMLResponse(content=html)
