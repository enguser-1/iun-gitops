"""
iun-uin-bridge v2.6 (2026-07-13) — Birth + Death events.
- UIN : 10 digits Verhoeff -> SN-XXXX-XXXX-XX
- BRN Birth : RRR-YYYY-NNNNNN
- DRN Death : RRR-YYYY-DNNNNNN (D prefix pour distinguer Death)
- v2.1 : detection region via DISTRICT
- v2.2 : ecrit ALSO national-id pour {{nationalId}}
- v2.3 : endpoint /certificate/birth/{patient_id}
- v2.4 : QR + /records + tracking + informant
- v2.5 : gestion Death events, mint UIN si absent + DRN. Endpoint /certificate/death/{id}
- v2.6 : fix noms (given vides -> double espace), layout cert (Delivre a vs Officier)
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
from fastapi import FastAPI
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
    log.info("iun-uin-bridge v2.6 demarre (DRY_RUN=%s, poll=%ss, uin=%s)",
             DRY_RUN, POLL_INTERVAL_S, UIN_SERVICE_URL)
    task = asyncio.create_task(poller())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


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
    child_gender = str(child.get("gender", ""))
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
  <title>IUN Senegal - Registre des actes de naissance</title>
  <style>
    :root {
      --sn-green: #00853F;
      --sn-yellow: #FDEF42;
      --sn-red: #E31B23;
      --gray-bg: #f4f6f4;
      --border: #d9e0d9;
    }
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 0; background: var(--gray-bg); color: #1a1a1a; }
    .flag { display: flex; height: 6px; }
    .flag > div { flex: 1; }
    .flag .g { background: var(--sn-green); }
    .flag .y { background: var(--sn-yellow); }
    .flag .r { background: var(--sn-red); }
    header { background: white; padding: 24px 40px; border-bottom: 2px solid var(--sn-green); }
    header h1 { margin: 0; color: var(--sn-green); font-size: 22px; }
    header .subtitle { color: #666; font-size: 13px; margin-top: 4px; }
    main { padding: 32px 40px; max-width: 1400px; margin: 0 auto; }
    .stats { display: flex; gap: 20px; margin-bottom: 24px; }
    .stat { background: white; padding: 16px 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); flex: 1; }
    .stat .val { font-size: 28px; font-weight: 700; color: var(--sn-green); }
    .stat .lbl { font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }
    table { width: 100%; background: white; border-collapse: collapse; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border-radius: 8px; overflow: hidden; }
    thead { background: var(--sn-green); color: white; }
    th, td { padding: 12px 16px; text-align: left; font-size: 14px; border-bottom: 1px solid var(--border); }
    tbody tr:hover { background: #f9fdf9; }
    td.brn, td.uin { font-family: "SF Mono", Menlo, Consolas, monospace; font-weight: 600; }
    td.uin { color: var(--sn-green); }
    a.btn { display: inline-block; padding: 6px 14px; background: var(--sn-green); color: white; text-decoration: none; border-radius: 4px; font-size: 13px; font-weight: 500; }
    a.btn:hover { background: #006630; }
    footer { text-align: center; padding: 20px; color: #999; font-size: 12px; }
    .empty { text-align: center; padding: 60px; color: #999; }
  </style>
</head>
<body>
  <div class="flag"><div class="g"></div><div class="y"></div><div class="r"></div></div>
  <header>
    <h1>REPUBLIQUE DU SENEGAL - Registre des actes de naissance</h1>
    <div class="subtitle">Systeme d'etat civil numerique IUN - Ministere de l'Interieur et de la Securite Publique</div>
  </header>
  <main>
    <div class="stats">
      <div class="stat"><div class="val">{{totalRecords}}</div><div class="lbl">Actes de naissance</div></div>
      <div class="stat"><div class="val">{{regionsUsed}}</div><div class="lbl">Regions couvertes</div></div>
      <div class="stat"><div class="val">{{today}}</div><div class="lbl">Genere le</div></div>
    </div>
    __TABLE__
  </main>
  <footer>IUN Senegal - v2.4 - {{now}}</footer>
</body>
</html>
"""


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
        gender = str(p.get("gender", ""))
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
    )
    return HTMLResponse(content=html)
