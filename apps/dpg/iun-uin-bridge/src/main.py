"""
iun-uin-bridge v4.0 (2026-08-20) — arbitrage A8 : MOSIP est le generateur
central et souverain d'identite. Le bridge ne fabrique plus d'identite : il la
RESOUT aupres du registre, et ne frappe que ce que le registre n'a pas.

  1. DECES : resolution AVANT frappe. Le defunt est cherche dans le registre
     (piece d'identite declaree, puis nom + date de naissance). Trouve -> on
     reprend SON UIN et SON VID, puis on desactive l'identite. Non trouve ou
     ambigu -> AUCUNE frappe : l'acte est numerote, et l'ecart est verse dans
     db.iun_ecarts. Un deces non rattache est une information utile ; un deces
     faussement rattache a un identifiant neuf est un mensonge propre.
  2. VID : emis par le registre (revocable, rotatif), plus par le bridge. La
     generation locale devient un mode degrade explicite (ALLOW_LOCAL_VID).
  3. IDENTITE : ecrite dans l'ID Repository a la naissance. Sans cela le
     registre distribuait un nombre sans savoir a qui.
  4. IDEMPOTENCE : la cle d'evenement (compositionId) est transmise au
     generateur. Deux chemins concurrents ne peuvent plus frapper deux fois.
  5. ROBUSTESSE : reprise, disjoncteur, reservation/confirmation. Un numero
     frappe puis perdu devient visible au lieu de disparaitre. Un enregistrement
     d'etat civil ne doit jamais echouer parce que le registre ne repond pas.

Historique v3.6 (2026-08-19) — acte de deces : lieu de deces fidele (etablissement OU domicile).
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
- v3.1 : norme MOSIP d'affichage — l'UIN (10 chiffres bruts) reste INTERNE (identifiant
  UIN_SYSTEM + BIRTH_CONFIGURABLE_IDENTIFIER_2), et c'est un VID (16 chiffres Verhoeff,
  service kernel si VID_SERVICE_URL sinon generation locale conforme) qui est AFFICHE
  partout : BIRTH_CONFIGURABLE_IDENTIFIER_1 = VID groupe XXXX-XXXX-XXXX-XXXX (acte natif),
  national-id = VID groupe (templates bridge + deces), /records. Mapping db.iun_vid_map.
- v3.2 : refonte nomenclature senegalaise — la hierarchie devient STATE=14 regions,
  DISTRICT=46 departements. La detection de region (codes BRN/DRN) remonte desormais au
  noeud STATE ; comparaison insensible aux accents (Thiès, Kédougou, Sédhiou...).
- v3.3 : Marriage event — sections `bride-details` + `groom-details` detectees, MRN commun
  au couple (RRR-YYYY-MNNNNNN, M prefix), sequence Mongo `mrn:{region}:{year}` independante.
  Bride et groom recoivent chacun UIN interne + VID affiche + MRN (si UIN absent, mint ;
  sinon adopt). Symetrique a Birth/Death mais avec 2 Patients writeback + un seul MRN.
- v3.4 : endpoint GET /certificate/death/{patient_id} — symetrique de /certificate/birth,
  gabarit `death-certificate.svg` (variante bridge du modele officiel SRMT, QR embarque en
  data URI). Champs remplis : deceased*, spouse*, informant*, placeOfDeath{Facility,District,
  Country}, registrationNumber = DRN, deceasedNationalId = VID affiche (norme v3.1).
  /records devient event-aware : un Patient porteur d'un DRN pointe vers l'acte de deces.
- v3.5 : UN SEUL DRN par deces. Le DRN officiel est alloue par POST /event-registration
  (il part dans confirmRegistration et atterrit sur la Task = numero vu par OpenCRVS et
  imprime sur l'acte natif). Le reconciliateur ne tire PLUS un second numero de sequence :
  il ADOPTE celui de db.iun_event_reg, sinon celui de la Task s'il est deja au format
  RRR-YYYY-DNNNNNN, et n'alloue une sequence qu'en dernier recours. Il complete ensuite
  l'entree iun_event_reg avec l'UIN/VID qu'il vient de minter. Compteur `drn_adoptions`.
- v3.6 : lieu de deces. OpenCRVS pose soit une Location HEALTH_FACILITY nommee, soit une
  Location SANS NOM de type DECEASED_USUAL_RESIDENCE / PRIVATE_HOME / OTHER dont l'adresse
  porte `district` et `state` sous forme d'UUID de Location. On rend donc : le nom de
  l'etablissement s'il existe, sinon un libelle lisible (« Domicile du defunt »…), et on
  resout les UUID d'adresse en noms. Plus AUCUN repli sur le bureau d'etat civil : un extrait
  ne peut pas laisser croire que la personne est decedee au guichet.
- Idempotence : presence UIN_SYSTEM
- Sequences : brn/drn/mrn:{region}:{year} dans db.iun_counters
"""

import asyncio
import base64
import io
import logging
import os
import re
import time
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
MRN_SYSTEM = "http://opencrvs.org/specs/id/marriage-registration-number"
MRN_TYPE_CODE = "MARRIAGE_REGISTRATION_NUMBER"
NATIONAL_ID_SYSTEM = "http://opencrvs.org/specs/id/national-id"
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:7070")
BCID1_TYPE_CODE = "BIRTH_CONFIGURABLE_IDENTIFIER_1"
BCID1_SYSTEM = "http://opencrvs.org/specs/id/birth-configurable-identifier-1"
BCID2_TYPE_CODE = "BIRTH_CONFIGURABLE_IDENTIFIER_2"
VID_SYSTEM = "http://iun.sn/specs/id/vid"
VID_TYPE_CODE = "VID"
VID_SERVICE_URL = os.environ.get("VID_SERVICE_URL", "")

# v4.0 — contrat MOSIP. Une seule base : le jour ou le kernel upstream tourne,
# c'est un repointage d'URL, pas une reecriture.
IDENTITY_SERVICE_URL = os.environ.get("IDENTITY_SERVICE_URL", UIN_SERVICE_URL)
IDREPO_ENABLED = os.environ.get("IDREPO_ENABLED", "true").lower() in ("1", "true", "yes")
# Mode degrade explicite : le VID fabrique par le bridge n'est pas revocable.
ALLOW_LOCAL_VID = os.environ.get("ALLOW_LOCAL_VID", "false").lower() in ("1", "true", "yes")
HTTP_RETRIES = int(os.environ.get("HTTP_RETRIES", "3"))
HTTP_BACKOFF_S = float(os.environ.get("HTTP_BACKOFF_S", "0.8"))
CB_THRESHOLD = int(os.environ.get("CB_THRESHOLD", "5"))
CB_COOLDOWN_S = int(os.environ.get("CB_COOLDOWN_S", "60"))
BRIDGE_VERSION = "v4.0"
REG_STATUS_REGISTERED = "REGISTERED"

EVENT_BIRTH = "birth-declaration"
EVENT_DEATH = "death-declaration"
EVENT_MARRIAGE = "marriage-declaration"

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
    "drn_adoptions": 0,
    "writebacks_ok": 0,
    "errors": 0,
    "last_cycle_at": None,
    "last_uin": None,
    "last_brn": None,
    "last_drn": None,
    "event_registrations": 0,
    "vids_minted": 0,
    "last_vid": None,
    "confirm_errors": 0,
    "adoptions": 0,
    "dry_run": DRY_RUN,
    # v4.0
    "deaths_resolved": 0,
    "deaths_unmatched": 0,
    "deaths_ambiguous": 0,
    "uins_deactivated": 0,
    "idrepo_writes": 0,
    "idrepo_errors": 0,
    "vids_from_registry": 0,
    "vids_local_degraded": 0,
    "uin_reservations_released": 0,
    "degraded_registrations": 0,
    "identity_service_open_circuit": False,
    "version": BRIDGE_VERSION,
}

# Disjoncteur du registre d'identite : au-dela de CB_THRESHOLD echecs consecutifs
# on cesse d'appeler pendant CB_COOLDOWN_S, au lieu de faire echouer chaque
# enregistrement d'etat civil derriere un service muet.
_cb = {"failures": 0, "open_until": 0.0}


def _cb_open():
    if _cb["open_until"] > time.time():
        return True
    if _cb["open_until"]:
        _cb["open_until"] = 0.0
        _cb["failures"] = 0
        state["identity_service_open_circuit"] = False
        log.info("registre d'identite : disjoncteur referme")
    return False


def _cb_ok():
    _cb["failures"] = 0


def _cb_ko():
    _cb["failures"] += 1
    if _cb["failures"] >= CB_THRESHOLD:
        _cb["open_until"] = time.time() + CB_COOLDOWN_S
        state["identity_service_open_circuit"] = True
        log.error("registre d'identite : disjoncteur OUVERT pour %ds (%d echecs)",
                  CB_COOLDOWN_S, _cb["failures"])


def get_db():
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return client.get_default_database()


def format_uin_sn(uin_10: str) -> str:
    u = re.sub(r"\D", "", str(uin_10))
    if len(u) != 10:
        raise ValueError(f"UIN attendu 10 digits, recu {len(u)}: {uin_10}")
    return f"SN-{u[0:4]}-{u[4:8]}-{u[8:10]}"


_VERHOEFF_D = [
    [0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],
    [3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],
    [6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],
    [9,8,7,6,5,4,3,2,1,0],
]
_VERHOEFF_P = [
    [0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],
    [8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],
    [2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8],
]
_VERHOEFF_INV = [0,4,3,2,1,5,6,7,8,9]


def verhoeff_check_digit(num_str):
    c = 0
    for i, ch in enumerate(reversed(str(num_str))):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return str(_VERHOEFF_INV[c])


def verhoeff_validate(num_str):
    c = 0
    for i, ch in enumerate(reversed(str(num_str))):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def gen_vid_local():
    """VID 16 chiffres conforme MOSIP : 1er chiffre 2-9, pas de sequences ni de
    triples repetitions, dernier chiffre = checksum Verhoeff."""
    import random
    rng = random.SystemRandom()
    for _ in range(64):
        body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(14))
        if re.search(r"(\d)\1\1", body):
            continue
        if any(abs(int(body[i + 1]) - int(body[i])) == 1 and abs(int(body[i + 2]) - int(body[i + 1])) == 1
               for i in range(len(body) - 2)):
            continue
        return body + verhoeff_check_digit(body)
    return body + verhoeff_check_digit(body)


def format_vid(vid_raw):
    v = re.sub(r"\D", "", str(vid_raw))
    return "-".join(v[i:i + 4] for i in range(0, len(v), 4)) if len(v) == 16 else str(vid_raw)


async def mint_vid_raw(client, uin_raw=None):
    """
    v4.0 : le VID est EMIS PAR LE REGISTRE, donc reellement revocable et rotatif.
    La generation locale devient un mode degrade explicite : elle produit un
    nombre au bon format que le registre ne connait pas, donc irrevocable.
    Elle est desactivee par defaut et comptabilisee quand elle sert.
    """
    vid = None
    if uin_raw:
        vid = await vid_for_uin(client, uin_raw)
    if not vid and VID_SERVICE_URL:
        # compat : ancien service VID autonome
        try:
            resp = await client.post("%s/v1/vidgenerator/vid" % VID_SERVICE_URL, timeout=15)
            resp.raise_for_status()
            cand = _unwrap(resp.json(), "vid")
            if cand and len(str(cand)) == 16 and str(cand).isdigit():
                vid = str(cand)
                state["vids_from_registry"] += 1
        except Exception as exc:
            log.warning("service VID autonome KO (%s)", exc)
    if not vid:
        if not ALLOW_LOCAL_VID:
            log.error("VID non emis pour UIN %s : le registre est le seul emetteur "
                      "(ALLOW_LOCAL_VID=false)", uin_raw)
            return None
        vid = gen_vid_local()
        state["vids_local_degraded"] += 1
        log.warning("MODE DEGRADE : VID %s genere localement — NON revocable, "
                    "inconnu du registre, a regulariser", vid)
    try:
        get_db()["iun_vid_map"].update_one(
            {"_id": vid},
            {"$set": {"uin": uin_raw, "ts": datetime.now(timezone.utc).isoformat(),
                      "source": "registry" if not ALLOW_LOCAL_VID or state["vids_local_degraded"] == 0 else "mixed"}},
            upsert=True,
        )
    except Exception as exc:
        log.warning("iun_vid_map KO : %s", exc)
    return vid


def _strip_accents(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn")


def region_code_for(name):
    if not name:
        return "XXX"
    key = _strip_accents(str(name).strip().lower())
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
    """Walk partOf : retourne le nom de la REGION administrative.
    v3.2 (nomenclature senegalaise) : region = noeud STATE (racine avant Location/0).
    Compat pre-refonte : si un STATE nomme Senegal est rencontre (ancienne hierarchie),
    on retourne le dernier noeud visite avant lui (l'ex-DISTRICT region)."""
    if not office_ref or "/" not in office_ref:
        return None
    loc_id = office_ref.split("/", 1)[1]
    last_name = None
    for _ in range(6):
        loc = db["Location"].find_one({"id": loc_id})
        if not loc:
            return last_name
        role = _location_role(loc)
        name = loc.get("name", "")
        if role == "STATE":
            # ancienne hierarchie : STATE = pays "Senegal" -> la region etait le niveau precedent
            if _strip_accents(name).strip().lower() == "senegal":
                return last_name
            return name
        last_name = name
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


# ═══════════════════════════════════════════════════════════════════════════
# v4.0 — CLIENT DU REGISTRE SOUVERAIN D'IDENTITE (contrat MOSIP)
# ═══════════════════════════════════════════════════════════════════════════

async def _id_call(client, method, path, json_body=None, timeout=12, allow_404=False):
    """
    Appel au registre. Ne leve jamais : renvoie (donnees, erreur).
    Reprise avec attente croissante, disjoncteur sur pannes consecutives.
    Une erreur 4xx est une reponse metier, pas une panne : elle ne compte pas
    pour le disjoncteur.
    """
    if _cb_open():
        return None, "circuit_open"
    url = "%s%s" % (IDENTITY_SERVICE_URL, path)
    last = "inconnu"
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            resp = await client.request(method, url, json=json_body, timeout=timeout)
            if resp.status_code in (200, 201):
                _cb_ok()
                return resp.json(), None
            if allow_404 and resp.status_code == 404:
                _cb_ok()
                return None, "not_found"
            if 400 <= resp.status_code < 500:
                _cb_ok()
                return None, "http_%s:%s" % (resp.status_code, resp.text[:160])
            last = "http_%s" % resp.status_code
        except Exception as exc:
            last = "%s: %s" % (type(exc).__name__, exc)
        if attempt < HTTP_RETRIES:
            await asyncio.sleep(HTTP_BACKOFF_S * attempt)
    _cb_ko()
    return None, last


def _unwrap(data, *keys):
    """Le registre repond en enveloppe MOSIP ; les versions anterieures a plat."""
    if not isinstance(data, dict):
        return None
    inner = data.get("response") if isinstance(data.get("response"), dict) else {}
    for k in keys:
        v = inner.get(k)
        if v in (None, ""):
            v = data.get(k)
        if v not in (None, ""):
            return v
    return None


def _patient_full_name(patient):
    try:
        n0 = (patient.get("name") or [{}])[0]
        parts = [p.strip() for p in (n0.get("given") or []) if p and p.strip()]
        fam = (n0.get("family") or "").strip()
        if fam:
            parts.append(fam)
        return " ".join(parts).strip()
    except Exception:
        return ""


def record_ecart(db, nature, comp_id, patient_id, regno, detail, criteria=None):
    """
    Un ecart n'est pas une panne : c'est un etat ou l'IUN et le registre ne
    disent pas la meme chose. On l'expose, on ne le normalise pas.
    """
    try:
        db["iun_ecarts"].update_one(
            {"_id": "%s:%s" % (nature, comp_id)},
            {"$set": {
                "nature": nature,
                "composition": comp_id,
                "patient": patient_id,
                "regno": regno,
                "detail": detail,
                "criteria": criteria or {},
                "state": "OPEN",
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        log.warning("ECART %s (%s) : %s", nature, regno or comp_id, detail)
    except Exception as exc:
        log.warning("enregistrement d'ecart KO : %s", exc)


async def mint_uin_raw(client, idem_key=None, requester="bridge-poller", reserve=False):
    """
    Frappe un UIN. Renvoie (uin, reservation_id).

    `idem_key` est l'identifiant de l'evenement declencheur : deux chemins
    d'attribution concurrents qui presentent la meme cle recoivent le meme
    numero. C'est ce qui ferme le mode de panne du double DRN, un cran plus haut.
    """
    body = {"requester": requester, "reserve": bool(reserve)}
    if idem_key:
        body["idempotencyKey"] = str(idem_key)[:160]
    data, err = await _id_call(client, "POST", "/v1/idgenerator/uin", body)
    if data is None:
        log.error("frappe UIN KO : %s", err)
        return None, None
    uin = _unwrap(data, "uin")
    if not (uin and len(str(uin)) == 10 and str(uin).isdigit()):
        log.error("reponse registre inattendue : %s", str(data)[:220])
        return None, None
    return str(uin), _unwrap(data, "reservationId")


async def confirm_uin(client, reservation_id, note=None):
    if not reservation_id:
        return
    await _id_call(client, "POST",
                   "/v1/idgenerator/reservation/%s/confirm" % reservation_id,
                   None, allow_404=True)


async def release_uin(client, reservation_id, note=None):
    """L'ecriture a echoue de notre cote : on relache. Le numero n'est jamais
    recycle, mais il cesse de compter comme une allocation en attente."""
    if not reservation_id:
        return
    _, err = await _id_call(client, "POST",
                            "/v1/idgenerator/reservation/%s/release" % reservation_id,
                            None, allow_404=True)
    if err is None:
        state["uin_reservations_released"] += 1


def _identity_payload(patient):
    """Jeu demographique minimal, issu de l'etat civil. Aucune biometrie."""
    out = {"fullName": _patient_full_name(patient)}
    if patient.get("birthDate"):
        out["dateOfBirth"] = str(patient["birthDate"])[:10]
    if patient.get("gender"):
        out["gender"] = str(patient["gender"])[:16]
    for ident in patient.get("identifier", []) or []:
        sysu = str(ident.get("system") or "")
        val = ident.get("value")
        if not val:
            continue
        if sysu == BRN_SYSTEM:
            out["birthRegistrationNumber"] = str(val)
        elif sysu == NATIONAL_ID_SYSTEM:
            digits = re.sub(r"\D", "", str(val))
            if digits and len(digits) not in (16,):
                out.setdefault("nationalId", digits)
    return out


async def idrepo_create(client, uin, patient, registration_id=None):
    """Ecrit l'identite derriere le numero. Sans cet appel, le registre a
    distribue un nombre sans savoir a qui."""
    if not IDREPO_ENABLED:
        return False
    payload = {"uin": str(uin), "identity": _identity_payload(patient),
               "registrationId": registration_id, "source": "opencrvs"}
    data, err = await _id_call(client, "POST", "/idrepository/v1/identity", payload)
    if data is None:
        state["idrepo_errors"] += 1
        log.warning("ID Repository : ecriture KO pour UIN %s (%s)", uin, err)
        return False
    state["idrepo_writes"] += 1
    log.info("ID Repository : identite ecrite pour UIN %s", uin)
    return True


async def idrepo_deactivate(client, uin, reason=None):
    """Le deces desactive l'identite et revoque ses VID. Sans cela le registre
    croyait le numero actif indefiniment."""
    if not IDREPO_ENABLED:
        return False
    data, err = await _id_call(
        client, "PATCH", "/idrepository/v1/identity/uin/%s" % uin,
        {"status": "DEACTIVATED", "reason": (reason or "deces")[:64]}, allow_404=True)
    if data is None:
        log.warning("desactivation KO pour UIN %s (%s)", uin, err)
        return False
    state["uins_deactivated"] += 1
    log.info("identite %s desactivee (%s)", uin, reason or "-")
    return True


def death_resolution_criteria(patient):
    """
    Ce qu'on presente au registre pour retrouver le defunt : d'abord la piece
    d'identite declaree au guichet, ensuite le nom et la date de naissance.
    """
    crit = {}
    _, nid = find_identifier(patient, system=NATIONAL_ID_SYSTEM)
    digits = re.sub(r"\D", "", str((nid or {}).get("value") or "")) if nid else ""
    if digits:
        if len(digits) == 16:
            crit["vid"] = digits
        elif len(digits) == 10:
            crit["uin"] = digits
        else:
            crit["nationalId"] = digits
            crit["cniNumber"] = digits
    name = _patient_full_name(patient)
    if name:
        crit["fullName"] = name
    if patient.get("birthDate"):
        crit["dateOfBirth"] = str(patient["birthDate"])[:10]
    return crit


async def resolve_identity(client, criteria):
    """Renvoie (candidat, verdict, tous_les_candidats). Ne tranche jamais seul :
    AMBIGUOUS et WEAK repartent en ecart."""
    if not criteria:
        return None, "NO_CRITERIA", []
    data, err = await _id_call(client, "POST",
                               "/idrepository/v1/identity/search", criteria)
    if data is None:
        return None, "SERVICE_UNAVAILABLE", []
    verdict = _unwrap(data, "verdict") or "NOT_FOUND"
    inner = data.get("response") if isinstance(data.get("response"), dict) else {}
    cands = inner.get("candidates") or data.get("candidates") or []
    if verdict in ("RESOLVED", "RESOLVED_DEMOGRAPHIC") and cands:
        return cands[0], verdict, cands
    return None, verdict, cands


async def vid_for_uin(client, uin):
    """VID actif du sujet : le registre rejoue le VID perpetuel existant."""
    data, err = await _id_call(client, "POST", "/v1/vidgenerator/vid",
                               {"uin": str(uin), "vidType": "PERPETUAL"})
    if data is None:
        log.warning("VID indisponible pour UIN %s (%s)", uin, err)
        return None
    vid = _unwrap(data, "vid")
    if vid and len(str(vid)) == 16 and str(vid).isdigit():
        state["vids_from_registry"] += 1
        return str(vid)
    return None


async def writeback_patient(client, patient_id, uin_value, brn_formatted, vid_raw=None):
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
            "value": uin_value,
        }
        if idx is not None:
            identifiers[idx] = uin_ident
        else:
            identifiers.append(uin_ident)

        # v3.1 : VID (norme MOSIP) — stocke brut, affiche groupe via national-id
        if vid_raw:
            idx, _ = find_identifier(patient, system=VID_SYSTEM)
            vid_ident = {
                "system": VID_SYSTEM,
                "type": {"coding": [{"system": "http://iun.sn/specs/identifier-type",
                                       "code": VID_TYPE_CODE}]},
                "value": vid_raw,
            }
            if idx is not None:
                identifiers[idx] = vid_ident
            else:
                identifiers.append(vid_ident)

        # national-id = valeur AFFICHEE sur les templates ({{nationalId}}/{{deceasedNationalId}})
        display_value = format_vid(vid_raw) if vid_raw else uin_value
        idx, _ = find_identifier(patient, system=NATIONAL_ID_SYSTEM)
        if idx is None:
            idx, _ = find_identifier(patient, type_code="NATIONAL_ID")
        nid_ident = {
            "system": NATIONAL_ID_SYSTEM,
            "type": {"coding": [{"system": "http://opencrvs.org/specs/identifier-type",
                                   "code": "NATIONAL_ID"}]},
            "value": display_value,
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


_DRN_RE = re.compile(r"^[A-Z]{3}-\d{4}-D\d{6}$")


def drn_from_task(task):
    """v3.5 : DRN deja pose sur la Task par confirmRegistration, s'il est a notre format."""
    if not task:
        return None
    for ident in task.get("identifier", []):
        s = str(ident.get("system", ""))
        if s.endswith("/death-registration-number"):
            v = str(ident.get("value", "") or "")
            if _DRN_RE.match(v):
                return v
    return None


def format_drn(region_code, year, seq):
    return f"{region_code}-{year:04d}-D{seq:06d}"


async def writeback_death(client, patient_id, uin_value, drn_formatted, vid_raw=None):
    """Writeback pour un Death : UIN interne + VID affiche + DRN."""
    try:
        r = await client.get(f"{HEARTH_URL}/fhir/Patient/{patient_id}", timeout=15)
        r.raise_for_status()
        patient = r.json()
        identifiers = patient.setdefault("identifier", [])

        # UIN (brut, interne) — v4.0 : absent si le deces n'a pas ete rattache
        if uin_value:
            idx, _ = find_identifier(patient, system=UIN_SYSTEM)
            uin_ident = {
                "system": UIN_SYSTEM,
                "type": {"coding": [{"system": "http://iun.sn/specs/identifier-type",
                                       "code": UIN_TYPE_CODE}]},
                "value": uin_value,
            }
            if idx is not None:
                identifiers[idx] = uin_ident
            else:
                identifiers.append(uin_ident)

        # v3.1 : VID (norme MOSIP)
        if vid_raw:
            idx, _ = find_identifier(patient, system=VID_SYSTEM)
            vid_ident = {
                "system": VID_SYSTEM,
                "type": {"coding": [{"system": "http://iun.sn/specs/identifier-type",
                                       "code": VID_TYPE_CODE}]},
                "value": vid_raw,
            }
            if idx is not None:
                identifiers[idx] = vid_ident
            else:
                identifiers.append(vid_ident)

        # national-id = valeur AFFICHEE ({{deceasedNationalId}})
        # v4.0 : si le deces n'est pas rattache, on ne TOUCHE PAS a la piece
        # d'identite declaree au guichet — c'est la seule cle de rapprochement
        # dont disposera l'agent qui instruira l'ecart.
        display_value = format_vid(vid_raw) if vid_raw else uin_value
        if display_value:
            idx, _ = find_identifier(patient, system=NATIONAL_ID_SYSTEM)
            nid_ident = {
                "system": NATIONAL_ID_SYSTEM,
                "type": {"coding": [{"system": "http://opencrvs.org/specs/identifier-type",
                                       "code": "NATIONAL_ID"}]},
                "value": display_value,
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
                # BCID1 = valeur affichee (VID groupe en v3.1, UIN formate en v3.0) ;
                # BCID2 = UIN brut (v3.1)
                _, bcid2 = find_identifier(patient, type_code=BCID2_TYPE_CODE)
                adopt_uin = (bcid2.get("value") if bcid2 else None) or bcid["value"]
                digits = re.sub(r"\D", "", str(bcid["value"]))
                adopt_vid = digits if len(digits) == 16 else None
                _, brn_id = find_identifier(patient, system=BRN_SYSTEM)
                if brn_id is None:
                    _, brn_id = find_identifier(patient, type_code=BRN_TYPE_CODE)
                adopt_brn = brn_id.get("value") if brn_id else None
                if DRY_RUN:
                    log.info("DRY_RUN adoption : Patient/%s UIN=%s VID=%s", patient_id, adopt_uin, adopt_vid or "-")
                    continue
                ok = await writeback_patient(client, patient_id, adopt_uin, adopt_brn, vid_raw=adopt_vid)
                if ok:
                    state["adoptions"] += 1
                    log.info("adoption v3 : UIN %s / VID %s -> identifiants pour Patient/%s",
                             adopt_uin, adopt_vid or "-", patient_id)
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

            # ══════════════════════════════════════════════════════════
            # v4.0 — la frappe n'est plus le reflexe par defaut.
            # Naissance : le sujet entre dans la vie civile, on frappe.
            # Deces     : le sujet existe deja quelque part, on RESOUT.
            # ══════════════════════════════════════════════════════════
            ok = False
            uin_fmt = None
            vid_raw = None
            reservation = None

            if event_type == EVENT_BIRTH:
                uin_raw, reservation = await mint_uin_raw(
                    client, idem_key=comp_id, requester="bridge-poller", reserve=True)
                if not uin_raw:
                    state["errors"] += 1
                    continue
                vid_raw = await mint_vid_raw(client, uin_raw)
                if vid_raw:
                    state["vids_minted"] += 1
                    state["last_vid"] = format_vid(vid_raw)
                uin_fmt = uin_raw  # UIN stocke BRUT (norme MOSIP), affichage = VID

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
                log.info("BIRTH UIN %s + VID %s + BRN %s pour Patient/%s (%s)",
                         uin_fmt, format_vid(vid_raw) if vid_raw else "-",
                         brn_fmt or "-", patient_id, name or "?")
                ok = await writeback_patient(client, patient_id, uin_fmt, brn_fmt, vid_raw=vid_raw)
                if ok:
                    # l'identite derriere le numero, sinon le registre ne sait pas a qui
                    await idrepo_create(client, uin_fmt, patient, registration_id=brn_fmt)
                    await confirm_uin(client, reservation, "writeback ok")
                else:
                    await release_uin(client, reservation, "writeback KO")

            elif event_type == EVENT_DEATH:
                # 1) le DRN : un seul par deces (v3.5, inchange)
                drn_fmt = None
                prev_reg = db["iun_event_reg"].find_one({"_id": comp_id})
                if prev_reg and prev_reg.get("regno") and str(prev_reg.get("event", "")).upper() == "DEATH":
                    drn_fmt = str(prev_reg["regno"])
                    state["drn_adoptions"] += 1
                    log.info("DEATH : DRN %s adopte depuis /event-registration (Composition %s)",
                             drn_fmt, comp_id)
                else:
                    drn_task = drn_from_task(task)
                    if drn_task:
                        drn_fmt = drn_task
                        state["drn_adoptions"] += 1
                        log.info("DEATH : DRN %s adopte depuis la Task (Composition %s)", drn_fmt, comp_id)
                    elif region_code != "XXX":
                        seq = next_drn_sequence(db, region_code, year)
                        drn_fmt = format_drn(region_code, year, seq)
                        state["drns_reformatted"] += 1
                        log.info("DEATH : aucun DRN prealable, allocation sequence -> %s", drn_fmt)
                    else:
                        log.warning("Patient/%s DEATH : region inconnue et aucun DRN prealable, DRN skip", patient_id)
                if drn_fmt:
                    state["last_drn"] = drn_fmt

                # 2) RESOUDRE AVANT DE FRAPPER
                criteria = death_resolution_criteria(patient)
                cand, verdict, cands = await resolve_identity(client, criteria)

                if cand:
                    uin_fmt = str(cand.get("uin"))
                    cand_status = str(cand.get("status") or "ACTIVE")
                    vid_raw = await vid_for_uin(client, uin_fmt) if cand_status == "ACTIVE" else None
                    state["deaths_resolved"] += 1
                    log.info("DEATH resolu (%s) : le defunt %s porte deja l'UIN %s [%s]",
                             verdict, name or patient_id, uin_fmt, cand_status)
                    ok = await writeback_death(client, patient_id, uin_fmt, drn_fmt, vid_raw=vid_raw)
                    if ok:
                        if cand_status == "ACTIVE":
                            # le numero cesse d'etre actif, et ses VID sont revoques
                            await idrepo_deactivate(client, uin_fmt, "deces %s" % (drn_fmt or comp_id))
                        else:
                            # l'identite etait deja desactivee : soit le deces a deja ete
                            # enregistre, soit deux actes visent le meme sujet. On relie
                            # les dossiers et on laisse l'humain trancher.
                            state["deaths_ambiguous"] += 1
                            record_ecart(
                                db, "deces_deja_enregistre", comp_id, patient_id, drn_fmt,
                                "l'identite %s est deja %s : un deces a-t-il deja ete "
                                "enregistre pour ce sujet ?" % (uin_fmt, cand_status),
                                criteria)
                else:
                    # 3) AUCUNE frappe. L'acte est numerote, l'ecart est expose.
                    nature = {
                        "AMBIGUOUS": "deces_ambigu",
                        "SERVICE_UNAVAILABLE": "registre_indisponible",
                    }.get(verdict, "deces_non_rattache")
                    if verdict == "AMBIGUOUS":
                        state["deaths_ambiguous"] += 1
                    elif verdict != "SERVICE_UNAVAILABLE":
                        state["deaths_unmatched"] += 1
                    detail = ("verdict=%s, %d candidat(s). Le defunt n'est pas rattache a une "
                              "identite du registre : aucun UIN n'est frappe. L'entite "
                              "detentrice doit instruire." % (verdict, len(cands)))
                    record_ecart(db, nature, comp_id, patient_id, drn_fmt, detail, criteria)
                    # on ecrit quand meme le DRN : l'acte de deces reste delivrable
                    ok = await writeback_death(client, patient_id, None, drn_fmt, vid_raw=None)

                if ok and prev_reg is not None:
                    try:
                        db["iun_event_reg"].update_one(
                            {"_id": comp_id},
                            {"$set": {"uin": uin_fmt, "vid": vid_raw,
                                      "resolution": verdict,
                                      "reconciled_at": datetime.now(timezone.utc).isoformat()}},
                        )
                    except Exception as _e:
                        log.warning("maj iun_event_reg %s KO : %s", comp_id, _e)
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
    log.info("iun-uin-bridge %s demarre (DRY_RUN=%s, poll=%ss)", BRIDGE_VERSION,
             DRY_RUN, POLL_INTERVAL_S)
    log.info("  registre d'identite : %s", IDENTITY_SERVICE_URL)
    log.info("  ID Repository       : %s", "actif" if IDREPO_ENABLED else "DESACTIVE")
    log.info("  VID local           : %s", "AUTORISE (mode degrade)" if ALLOW_LOCAL_VID
             else "interdit - le registre est le seul emetteur")
    log.info("  reprise             : %d tentatives, disjoncteur a %d echecs (%ds)",
             HTTP_RETRIES, CB_THRESHOLD, CB_COOLDOWN_S)
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
            prev_vid = prev.get("vid")
            if uin_fmt:
                if prev_vid:
                    identifiers = [
                        {"type": BCID1_TYPE_CODE, "value": format_vid(prev_vid)},
                        {"type": BCID2_TYPE_CODE, "value": uin_fmt},
                    ]
                else:
                    identifiers = [{"type": BCID1_TYPE_CODE, "value": uin_fmt}]
            log.info("/event-registration retry : reutilise regno=%s uin=%s vid=%s (%s)",
                     regno, uin_fmt or "-", prev_vid or "-", composition_id)
        elif DRY_RUN:
            log.info("/event-registration DRY_RUN : passthrough regno=%s (%s)", regno, event_code)
        else:
            if event_code == "BIRTH":
                # v4.0 : la cle d'idempotence est l'evenement lui-meme. Le webhook
                # mosip-api et ce chemin ne peuvent plus frapper deux fois.
                uin_raw, _resa = await mint_uin_raw(
                    client, idem_key=composition_id, requester="bridge-event", reserve=False)
                if not uin_raw:
                    # DEGRADATION GRACIEUSE : un enregistrement d'etat civil ne doit
                    # jamais echouer parce que le registre d'identite ne repond pas.
                    # On confirme l'enregistrement avec son numero d'acte ; le
                    # reconciliateur adoptera l'UIN au prochain cycle.
                    state["degraded_registrations"] += 1
                    log.error("registre injoignable : enregistrement %s confirme SANS UIN, "
                              "reconciliation differee", composition_id)
                    if region_code != "XXX":
                        seq = next_brn_sequence(db, region_code, year)
                        regno = format_brn(region_code, year, seq)
                        state["last_brn"] = regno
                    db["iun_event_reg"].replace_one(
                        {"_id": composition_id},
                        {"_id": composition_id, "regno": regno, "uin": None, "vid": None,
                         "event": event_code, "tracking_id": tracking_id,
                         "degraded": True,
                         "ts": datetime.now(timezone.utc).isoformat()},
                        upsert=True,
                    )
                    record_ecart(db, "identite_differee", composition_id, None, regno,
                                 "registre d'identite injoignable a l'enregistrement ; "
                                 "l'UIN sera attribue par le reconciliateur")
                    ok, err = await confirm_registration(client, composition_id, regno, None, auth)
                    if not ok:
                        state["confirm_errors"] += 1
                        return _boom_500("bridge IUN : confirmRegistration KO : %s" % err)
                    from fastapi.responses import Response as _Resp
                    return _Resp(status_code=202)
                vid_raw = await mint_vid_raw(client, uin_raw)
                uin_fmt = uin_raw  # v3.1 : UIN stocke BRUT, jamais imprime
                # norme MOSIP : BCID1 = VID groupe (affiche sur l'acte), BCID2 = UIN brut (interne)
                identifiers = [
                    {"type": BCID1_TYPE_CODE, "value": format_vid(vid_raw)},
                    {"type": BCID2_TYPE_CODE, "value": uin_raw},
                ]
                state["vids_minted"] += 1
                state["last_vid"] = format_vid(vid_raw)
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
                 "vid": (locals().get("vid_raw") if event_code == "BIRTH" else None),
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


@app.get("/ecarts")
def ecarts(state_filter: str = "OPEN", limit: int = 100):
    """
    Registre des ecarts de reconciliation. Un ecart n'est pas une panne : c'est
    un etat ou l'etat civil et le registre d'identite ne disent pas la meme
    chose. Le bridge l'expose, il ne le tranche pas.
    """
    try:
        db = get_db()
        q = {} if state_filter in ("", "ALL") else {"state": state_filter}
        rows = list(db["iun_ecarts"].find(q).sort("detected_at", -1).limit(int(limit)))
        for r in rows:
            r["id"] = r.pop("_id", None)
        return {"count": len(rows), "state": state_filter, "ecarts": rows}
    except Exception as exc:
        return {"count": 0, "error": str(exc), "ecarts": []}


@app.get("/vid-preview")
def vid_preview():
    """Genere un VID local (test) : verifie la conformite Verhoeff."""
    v = gen_vid_local()
    return {
        "vid_raw": v,
        "vid_display": format_vid(v),
        "longueur": len(v),
        "verhoeff_ok": verhoeff_validate(v),
        "source": "vid-service" if VID_SERVICE_URL else "generation-locale",
    }


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

# --- v3.4 : gabarit du certificat de deces (variante bridge, QR en data URI) ---
try:
    with open(os.path.join(os.path.dirname(__file__), "death-certificate.svg"), "r", encoding="utf-8") as _f:
        DEATH_SVG_TEMPLATE = _f.read()
    log.info("SVG template deces charge (%d chars)", len(DEATH_SVG_TEMPLATE))
except Exception as _e:
    log.warning("SVG template deces non trouve : %s (le endpoint /certificate/death renverra 500)", _e)
    DEATH_SVG_TEMPLATE = None


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


_FR_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
              "août", "septembre", "octobre", "novembre", "décembre")


def _fr_long_date(iso_date):
    """2026-08-19 -> 19 août 2026 (equivalent du helper handlebars frLongDate cote client)."""
    try:
        d = datetime.strptime(str(iso_date)[:10], "%Y-%m-%d")
        return "%d %s %d" % (d.day, _FR_MONTHS[d.month - 1], d.year)
    except Exception:
        return str(iso_date or "")


# v3.6 : libelles des lieux de deces non nommes (Location sans `name` cote OpenCRVS)
_PLACE_LABELS = {
    "DECEASED_USUAL_RESIDENCE": "Domicile du défunt",
    "PRIVATE_HOME": "Domicile",
    "OTHER": "Autre lieu",
    "HEALTH_FACILITY": "Établissement de santé",
}
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _resolve_place_name(db, value):
    """Un champ d'adresse OpenCRVS peut contenir un UUID de Location plutot qu'un libelle."""
    v = str(value or "").strip()
    if not v:
        return ""
    if _UUID_RE.match(v):
        loc = db["Location"].find_one({"id": v})
        return (loc.get("name") or "").strip() if loc else ""
    return v


def _find_place_of_death(db, comp):
    """Section death-encounter -> Encounter -> Location : (lieu, departement, pays).

    Deux formes possibles cote OpenCRVS :
      - HEALTH_FACILITY : Location nommee, hierarchie via partOf ;
      - DECEASED_USUAL_RESIDENCE / PRIVATE_HOME / OTHER : Location SANS nom, dont
        address.district / address.state sont des UUID de Location.
    """
    facility = district = country = ""
    if not comp:
        return facility, district, country
    for section in comp.get("section", []):
        codings = (section.get("code") or {}).get("coding", [])
        if not any(c.get("code") == "death-encounter" for c in codings):
            continue
        entries = section.get("entry", [])
        if not entries or not entries[0].get("reference", "").startswith("Encounter/"):
            break
        enc = db["Encounter"].find_one({"id": entries[0]["reference"].split("/", 1)[1]})
        if not enc:
            break
        for loc in enc.get("location", []):
            ref = (loc.get("location") or {}).get("reference", "")
            if not ref.startswith("Location/"):
                continue
            lres = db["Location"].find_one({"id": ref.split("/", 1)[1]})
            if not lres:
                continue
            addr = lres.get("address") or {}
            types = [str(x.get("code") or "") for x in ((lres.get("type") or {}).get("coding") or [])]

            facility = (lres.get("name") or "").strip()
            if not facility:
                for t in types:
                    if t in _PLACE_LABELS:
                        facility = _PLACE_LABELS[t]
                        break

            # departement : l'adresse d'abord (UUID resolu), sinon le parent hierarchique
            district = _resolve_place_name(db, addr.get("district"))
            if not district:
                parent_ref = ((lres.get("partOf") or {}).get("reference") or "")
                if parent_ref.startswith("Location/"):
                    parent = db["Location"].find_one({"id": parent_ref.split("/", 1)[1]})
                    if parent:
                        district = (parent.get("name") or "").strip()
            country = str(addr.get("country") or "").strip()
            break
        break
    return facility, district, country


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

    uin = brn = vid = ""
    for ident in child.get("identifier", []):
        s = ident.get("system", "")
        v = str(ident.get("value", ""))
        if s == UIN_SYSTEM: uin = v
        elif s == BRN_SYSTEM: brn = v
        elif s == VID_SYSTEM: vid = v
    # v3.1 norme MOSIP : on AFFICHE le VID, l'UIN reste interne
    if vid:
        uin = format_vid(vid)

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


@app.get("/certificate/death/{patient_id}")
async def render_death_certificate(patient_id: str):
    """v3.4 : rend l'acte de deces SVG rempli avec les donnees FHIR du Patient decede."""
    from fastapi.responses import Response, PlainTextResponse
    if DEATH_SVG_TEMPLATE is None:
        return PlainTextResponse("SVG template deces absent du deployment", status_code=500)
    db = get_db()
    dec = db["Patient"].find_one({"id": patient_id})
    if not dec:
        return PlainTextResponse(f"Patient {patient_id} not found in Hearth", status_code=404)

    dec_given, dec_family = _patient_display_name(dec)
    dec_gender = {"male": "Masculin", "female": "Féminin"}.get(str(dec.get("gender", "")), str(dec.get("gender", "")))
    death_date = str(dec.get("deceasedDateTime") or dec.get("deceasedDate") or "")[:10]

    uin = drn = vid = ""
    for ident in dec.get("identifier", []):
        s = ident.get("system", "")
        v = str(ident.get("value", ""))
        if s == UIN_SYSTEM:
            uin = v
        elif s == DRN_SYSTEM:
            drn = v
        elif s == VID_SYSTEM:
            vid = v
    # v3.1 norme MOSIP : on AFFICHE le VID, l'UIN 10 chiffres reste interne
    if vid:
        uin = format_vid(vid)

    comp = _find_composition_for_patient(db, patient_id)
    spouse_given = spouse_family = ""
    informant_given = informant_family = ""
    reg_date = ""
    facility = district = country = ""
    if comp:
        spouse = _find_related_patient(db, comp["id"], "spouse-details")
        spouse_given, spouse_family = _patient_display_name(spouse)
        informant = _find_informant(db, comp["id"])
        informant_given, informant_family = _patient_display_name(informant)
        reg_date = str((comp.get("date") or ""))[:10]
        facility, district, country = _find_place_of_death(db, comp)
        if not death_date:
            death_date = reg_date
    if str(country).strip().upper() in ("SEN", "SN", ""):
        country = "Sénégal"

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reg_office_name = "Bureau Etat Civil Dakar Plateau"

    verify_url = f"https://iun-uin-cert-dev.apps.origins.heritage.africa/certificate/death/{patient_id}"
    qr_uri = _make_qr_data_uri(verify_url)

    replacements = {
        "deceasedFirstName": dec_given,
        "deceasedFamilyName": dec_family or "-",
        "deceasedGender": dec_gender or "-",
        "deceasedNationalId": uin or "-",
        "eventDate": death_date or "-",
        "placeOfDeathFacility": facility or "-",
        "placeOfDeathDistrict": district or "-",
        "placeOfDeathCountry": country,
        "spouseFirstName": spouse_given,
        "spouseFamilyName": spouse_family or "-",
        "informantFirstName": informant_given,
        "informantFamilyName": informant_family or "-",
        "registrationNumber": drn or "-",
        "registrationLocation": reg_office_name,
        "registrationDate": reg_date or now_iso,
        "certificateDate": _fr_long_date(now_iso),
        "registrarName": "Officier d'Etat Civil",
        "qrCodeDataUri": qr_uri,
    }
    svg = DEATH_SVG_TEMPLATE
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
        uin = brn = vid = drn = ""
        for ident in p.get("identifier", []):
            s = ident.get("system", "")
            v = str(ident.get("value", ""))
            if s == UIN_SYSTEM: uin = v
            elif s == BRN_SYSTEM: brn = v
            elif s == DRN_SYSTEM: drn = v
            elif s == VID_SYSTEM: vid = v
        if vid:
            uin = format_vid(vid)  # v3.1 : affichage = VID
        # v3.4 : un Patient porteur d'un DRN est un acte de deces
        regno = drn or brn
        if regno and "-" in regno:
            regions.add(regno.split("-", 1)[0])
        pid = p.get("id", "")
        if drn:
            acte = "Décès"
            cert_url = f"/certificate/death/{pid}"
            event_date = str(p.get("deceasedDateTime") or "")[:10]
        else:
            acte = "Naissance"
            cert_url = f"/certificate/birth/{pid}"
            event_date = str(p.get("birthDate", ""))
        gender = {"male": "Masculin", "female": "Féminin"}.get(str(p.get("gender", "")), str(p.get("gender", "")))
        rows.append(
            f'<tr>'
            f'<td>{full}</td>'
            f'<td>{acte}</td>'
            f'<td>{gender}</td>'
            f'<td>{event_date or "-"}</td>'
            f'<td class="brn">{regno or "-"}</td>'
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
        .replace("__VERSION__", "v4.0")
    )
    return HTMLResponse(content=html)
