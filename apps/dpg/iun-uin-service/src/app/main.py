"""
IUN Identity Service - generateur central et souverain d'identite du Senegal.

v1.0 (03/06/2026) : generateur d'UIN, remplacement drop-in de
                    MOSIP kernel-idgenerator-service.
v2.0 (20/08/2026) : le service devient le REGISTRE souverain (arbitrage A8).
                    Il ne distribue plus seulement un nombre : il detient
                    l'identite derriere le nombre, emet les VID et porte le
                    cycle de vie de l'identifiant.

Les chemins exposes restent ceux de MOSIP. Le jour ou le kernel upstream
tourne, c'est un repointage d'URL cote appelant, pas une reecriture :

  POST   /v1/idgenerator/uin                     -> frappe un UIN (idempotent)
  POST   /v1/idgenerator/reservation/{id}/confirm-> confirme l'allocation
  POST   /v1/idgenerator/reservation/{id}/release-> relache (echec appelant)
  GET    /v1/idgenerator/uin/{uin}/validate
  GET    /v1/idgenerator/uin/{uin}/explain

  POST   /v1/vidgenerator/vid                    -> emet un VID pour un UIN
  GET    /v1/vidgenerator/vid/{vid}              -> resout un VID
  PATCH  /v1/vidgenerator/vid/{vid}              -> revoque

  POST   /idrepository/v1/identity               -> cree l'identite
  GET    /idrepository/v1/identity/uin/{uin}     -> lit l'identite
  PATCH  /idrepository/v1/identity/uin/{uin}     -> met a jour / desactive
  POST   /idrepository/v1/identity/search        -> RESOUT avant de frapper

  GET    /actuator/health · /actuator/readiness
  GET    /v1/admin/reservations/stale            -> numeros frappes non confirmes
  POST   /v1/admin/uin/import                    -> reprise d'un UIN anterieur
  POST   /v1/admin/vid/import                    -> reprise d'un VID deja imprime

v2.1 (20/08/2026) : deux chemins de reprise, pour rattraper les identifiants
distribues avant que le registre n'existe. On n'emet pas de numero neuf a la
place d'un numero deja rendu au citoyen.
"""
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

import asyncpg
from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field

from .verhoeff import validate
from .generator import generate_uin
from .filters import passes_all_filters, explain_filters
from .vid import generate_vid, format_vid
from . import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
)
logger = logging.getLogger("iun-identity-service")

SERVICE_VERSION = "2.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup : initializing DB schema (v2 - registre souverain)")
    await db.init_schema()
    logger.info("startup : ready")
    yield
    logger.info("shutdown : closing DB pool")
    await db.close_pool()


app = FastAPI(
    title="IUN Identity Service",
    version=SERVICE_VERSION,
    description=(
        "Generateur central et souverain d'identite du Senegal. "
        "UIN Verhoeff 10 chiffres (ADR-R01), VID 16 chiffres revocables, "
        "registre d'identite et cycle de vie. API compatible MOSIP."
    ),
    lifespan=lifespan,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def envelope(op: str, response: Any, errors=None) -> Dict[str, Any]:
    """Enveloppe MOSIP. Les champs plats sont conserves en doublon tant que
    des appelants v3.x lisent la reponse a plat."""
    body = {
        "id": "sn.iun.%s" % op,
        "version": "v1",
        "responsetime": now_iso(),
        "response": response,
        "errors": errors,
    }
    if isinstance(response, dict):
        for k, v in response.items():
            body.setdefault(k, v)
    return body


def fail(code: int, err: str, msg: str):
    raise HTTPException(code, detail={"errorCode": err, "message": msg})


# ─────────────────────────────────────────────────────────────────────────────
# Sante
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/actuator/health")
async def health():
    db_ok = await db.health_check()
    if not db_ok:
        return {"status": "DOWN", "db_reachable": False, "issued_count": 0}
    return {
        "status": "UP",
        "version": SERVICE_VERSION,
        "db_reachable": True,
        "issued_count": await db.count_issued(),
        "identity_count": await db.count_identities(),
        "vid_active_count": await db.count_vids("ACTIVE"),
        "stale_reservations": len(await db.stale_reservations(30)),
    }


@app.get("/actuator/readiness")
async def readiness():
    if not await db.health_check():
        raise HTTPException(503, "DB not ready")
    return {"status": "READY"}


# ─────────────────────────────────────────────────────────────────────────────
# UIN
# ─────────────────────────────────────────────────────────────────────────────

class UINRequest(BaseModel):
    idempotencyKey: Optional[str] = Field(
        None,
        description="Identifiant de l'evenement declencheur (ex. Composition FHIR). "
                    "Deux appels avec la meme cle rendent le meme UIN.",
    )
    requester: Optional[str] = Field(None, description="Appelant, pour le journal")
    reserve: bool = Field(
        False,
        description="Si vrai, l'UIN est RESERVE et doit etre confirme. "
                    "Un numero reserve jamais confirme est visible dans "
                    "/v1/admin/reservations/stale au lieu d'etre perdu.",
    )


@app.post("/v1/idgenerator/uin")
async def generate(body: Optional[UINRequest] = Body(None)):
    """
    Frappe un UIN conforme (Verhoeff + filtres MOSIP), unique en registre.

    Deux protections ajoutees en v2 :
      - **idempotence** : deux chemins d'attribution concurrents (webhook et
        poller) qui presentent la meme cle recoivent le meme numero ;
      - **reservation** : l'appelant confirme apres avoir ecrit de son cote,
        ce qui rend une fuite de numero visible plutot que silencieuse.
    """
    req = body or UINRequest()

    if req.idempotencyKey:
        prev = await db.reservation_by_key(req.idempotencyKey)
        if prev:
            logger.info("idempotence : cle=%s -> UIN %s deja alloue (%s)",
                        req.idempotencyKey, prev["uin"], prev["state"])
            return envelope("idgenerator.uin", {
                "uin": prev["uin"],
                "length": 10,
                "check_digit": int(prev["uin"][-1]),
                "spec": "MOSIP-Verhoeff length=10 (ADR-R01 Senegal)",
                "attempts": 0,
                "reservationId": str(prev["reservation_id"]),
                "state": prev["state"],
                "replayed": True,
            })

    for _ in range(5):
        uin, attempts = generate_uin()
        try:
            await db.persist_uin(uin, metadata={"attempts": attempts,
                                                "requester": req.requester})
        except asyncpg.UniqueViolationError:
            logger.warning("UIN %s collision - retry", uin)
            continue

        reservation_id = str(uuid.uuid4())
        state = "RESERVED" if req.reserve else "CONFIRMED"
        try:
            await db.reserve_uin(reservation_id, uin, req.idempotencyKey, req.requester)
            if state == "CONFIRMED":
                await db.settle_reservation(reservation_id, "CONFIRMED",
                                            "confirme a l'emission")
        except asyncpg.UniqueViolationError:
            # course sur la cle d'idempotence : l'autre chemin a gagne
            prev = await db.reservation_by_key(req.idempotencyKey)
            if prev:
                return envelope("idgenerator.uin", {
                    "uin": prev["uin"], "length": 10,
                    "check_digit": int(prev["uin"][-1]),
                    "attempts": 0, "reservationId": str(prev["reservation_id"]),
                    "state": prev["state"], "replayed": True,
                })
            raise

        logger.info("UIN %s frappe (tentatives=%d, etat=%s, cle=%s)",
                    uin, attempts, state, req.idempotencyKey or "-")
        return envelope("idgenerator.uin", {
            "uin": uin,
            "length": 10,
            "check_digit": int(uin[-1]),
            "spec": "MOSIP-Verhoeff length=10 (ADR-R01 Senegal)",
            "attempts": attempts,
            "reservationId": reservation_id,
            "state": state,
            "replayed": False,
        })

    fail(500, "IDG-001", "Aucun UIN unique apres 5 tentatives")


@app.post("/v1/idgenerator/reservation/{reservation_id}/confirm")
async def confirm_reservation(reservation_id: str, note: Optional[str] = None):
    ok = await db.settle_reservation(reservation_id, "CONFIRMED", note)
    if not ok:
        fail(404, "IDG-002", "Reservation inconnue ou deja reglee")
    return envelope("idgenerator.confirm", {"reservationId": reservation_id,
                                            "state": "CONFIRMED"})


@app.post("/v1/idgenerator/reservation/{reservation_id}/release")
async def release_reservation(reservation_id: str, note: Optional[str] = None):
    """
    L'appelant n'a pas pu ecrire de son cote : on relache. Le numero n'est pas
    reattribue - on ne recycle jamais un identifiant - mais il cesse de compter
    comme une allocation en attente.
    """
    ok = await db.settle_reservation(reservation_id, "RELEASED", note)
    if not ok:
        fail(404, "IDG-002", "Reservation inconnue ou deja reglee")
    return envelope("idgenerator.release", {"reservationId": reservation_id,
                                            "state": "RELEASED"})


@app.get("/v1/idgenerator/uin/{uin}/validate")
async def validate_uin(uin: str = Path(..., min_length=10, max_length=10, pattern=r"^\d{10}$")):
    return {
        "uin": uin,
        "valid_check_digit": validate(uin),
        "passes_filters": passes_all_filters(uin),
        "exists_in_registry": await db.uin_exists(uin),
    }


@app.get("/v1/idgenerator/uin/{uin}/explain")
async def explain(uin: str = Path(..., min_length=10, max_length=10, pattern=r"^\d{10}$")):
    return {"uin": uin, "verhoeff_valid": validate(uin), "filters": explain_filters(uin)}


@app.get("/v1/admin/reservations/stale")
async def stale(minutes: int = 30):
    """Numeros frappes et jamais confirmes. Doit rester a zero."""
    rows = await db.stale_reservations(minutes)
    return envelope("admin.stale", {
        "olderThanMinutes": minutes,
        "count": len(rows),
        "reservations": [
            {"reservationId": str(r["reservation_id"]), "uin": r["uin"],
             "idempotencyKey": r["idempotency_key"], "requester": r["requester"],
             "reservedAt": r["reserved_at"].isoformat() if r["reserved_at"] else None}
            for r in rows
        ],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Identite
# ─────────────────────────────────────────────────────────────────────────────

class IdentityCreate(BaseModel):
    uin: str = Field(..., pattern=r"^\d{10}$")
    identity: Dict[str, Any] = Field(
        ...,
        description="Jeu demographique minimal : fullName, dateOfBirth, gender, "
                    "placeOfBirth, nationalId, cniNumber, birthRegistrationNumber…",
    )
    registrationId: Optional[str] = None
    source: str = "opencrvs"


class IdentityPatch(BaseModel):
    identity: Optional[Dict[str, Any]] = None
    status: Optional[str] = Field(None, pattern=r"^(ACTIVE|DEACTIVATED|MERGED)$")
    reason: Optional[str] = None


class IdentitySearch(BaseModel):
    uin: Optional[str] = None
    vid: Optional[str] = None
    nationalId: Optional[str] = None
    cniNumber: Optional[str] = None
    birthRegistrationNumber: Optional[str] = None
    fullName: Optional[str] = None
    dateOfBirth: Optional[str] = None
    yearOfBirth: Optional[str] = None
    limit: int = 10


def _identity_out(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "uin": row["uin"],
        "status": row["status"],
        "identity": row["identity"],
        "registrationId": row.get("registration_id"),
        "source": row.get("source"),
        "createdAt": row["created_at"].isoformat() if row.get("created_at") else None,
        "updatedAt": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "deactivatedAt": row["deactivated_at"].isoformat() if row.get("deactivated_at") else None,
        "deactivationReason": row.get("deactivation_reason"),
        **({"matchType": row["matchType"], "score": row["score"]} if "matchType" in row else {}),
    }


@app.post("/idrepository/v1/identity", status_code=201)
async def identity_create(body: IdentityCreate):
    """
    Cree l'identite derriere le numero. Sans cet appel, le registre a distribue
    un nombre sans savoir a qui - c'est l'etat dans lequel se trouvait la
    plateforme jusqu'au 20/08/2026.
    """
    if not await db.uin_exists(body.uin):
        fail(400, "IDR-001", "UIN inconnu du registre : frappez-le d'abord")
    row = await db.identity_create(body.uin, body.identity, body.registrationId, body.source)
    logger.info("identite creee pour UIN %s (regId=%s)", body.uin, body.registrationId)
    return envelope("idrepository.create", _identity_out(row))


@app.get("/idrepository/v1/identity/uin/{uin}")
async def identity_read(uin: str = Path(..., pattern=r"^\d{10}$")):
    row = await db.identity_get(uin)
    if row is None:
        fail(404, "IDR-002", "Aucune identite pour cet UIN")
    return envelope("idrepository.read", _identity_out(row))


@app.patch("/idrepository/v1/identity/uin/{uin}")
async def identity_patch(uin: str, body: IdentityPatch):
    """
    Met a jour l'identite, ou change son statut.

    `status=DEACTIVATED` est ce qui manquait au deces : le numero cessait
    d'etre servi par l'etat civil, mais le registre le croyait actif
    indefiniment. La desactivation revoque aussi les VID actifs - un
    identifiant d'echange ne survit pas a son sujet.
    """
    row = await db.identity_get(uin)
    if row is None:
        fail(404, "IDR-002", "Aucune identite pour cet UIN")
    if body.identity:
        row = await db.identity_update(uin, body.identity)
    if body.status:
        row = await db.identity_set_status(uin, body.status, body.reason)
        logger.info("identite %s -> %s (%s)", uin, body.status, body.reason or "-")
    return envelope("idrepository.patch", _identity_out(row))


@app.post("/idrepository/v1/identity/search")
async def identity_search(body: IdentitySearch):
    """
    **Resoudre avant de frapper.**

    Rend des candidats classes, avec le type de correspondance. Ne fusionne
    jamais et ne tranche jamais : c'est a l'appelant de decider, et de verser
    en ecart ce qui reste ambigu. Un deces non rattache est une information
    utile ; un deces faussement rattache a un identifiant neuf est un
    mensonge propre.
    """
    crit = body.dict(exclude_none=True)
    limit = crit.pop("limit", 10)
    if not crit:
        fail(400, "IDR-003", "Aucun critere de resolution fourni")
    rows = await db.identity_search(crit, limit=limit)
    exact = [r for r in rows if r["score"] >= 1.0]
    strong = [r for r in rows if 0.8 <= r["score"] < 1.0]
    if exact:
        verdict = "RESOLVED"
    elif len(strong) == 1:
        verdict = "RESOLVED_DEMOGRAPHIC"
    elif strong:
        verdict = "AMBIGUOUS"
    elif rows:
        verdict = "WEAK"
    else:
        verdict = "NOT_FOUND"
    return envelope("idrepository.search", {
        "verdict": verdict,
        "count": len(rows),
        "candidates": [_identity_out(r) for r in rows],
    })


# ─────────────────────────────────────────────────────────────────────────────
# VID
# ─────────────────────────────────────────────────────────────────────────────

class VIDRequest(BaseModel):
    uin: str = Field(..., pattern=r"^\d{10}$")
    vidType: str = Field("PERPETUAL", pattern=r"^(PERPETUAL|TEMPORARY)$")
    ttlHours: Optional[int] = Field(None, description="Duree de vie d'un VID temporaire")
    rotate: bool = Field(False, description="Revoque le VID perpetuel courant et en emet un neuf")


class VIDPatch(BaseModel):
    status: str = Field(..., pattern=r"^REVOKED$")
    reason: Optional[str] = None


@app.post("/v1/vidgenerator/vid", status_code=201)
async def vid_create(body: VIDRequest):
    """
    Emet un VID pour un UIN. Emis ici, donc reellement revocable et rotatif -
    ce que la generation locale cote bridge ne permettait pas.
    """
    ident = await db.identity_get(body.uin)
    if ident is None and not await db.uin_exists(body.uin):
        fail(400, "VID-001", "UIN inconnu du registre")
    if ident is not None and ident["status"] != "ACTIVE":
        fail(409, "VID-002", "Identite %s : aucun VID pour un sujet %s"
             % (body.uin, ident["status"]))

    if body.vidType == "PERPETUAL":
        current = await db.vid_active_for(body.uin, "PERPETUAL")
        if current and not body.rotate:
            return envelope("vidgenerator.vid", {
                "vid": current["vid"], "formatted": format_vid(current["vid"]),
                "uin": body.uin, "vidType": "PERPETUAL", "status": "ACTIVE",
                "replayed": True,
            })
        if current and body.rotate:
            await db.vid_revoke(current["vid"], "rotation")

    expires_at = None
    if body.vidType == "TEMPORARY":
        expires_at = datetime.now(timezone.utc) + timedelta(hours=body.ttlHours or 24)

    for _ in range(5):
        vid, _attempts = generate_vid()
        try:
            row = await db.vid_persist(vid, body.uin, body.vidType, expires_at)
        except asyncpg.UniqueViolationError:
            continue
        logger.info("VID %s emis pour UIN %s (%s)", vid, body.uin, body.vidType)
        return envelope("vidgenerator.vid", {
            "vid": row["vid"], "formatted": format_vid(row["vid"]),
            "uin": body.uin, "vidType": row["vid_type"], "status": row["status"],
            "expiresAt": row["expires_at"].isoformat() if row["expires_at"] else None,
            "replayed": False,
        })
    fail(500, "VID-003", "Aucun VID unique apres 5 tentatives")


@app.get("/v1/vidgenerator/vid/{vid}")
async def vid_resolve(vid: str = Path(..., pattern=r"^\d{16}$")):
    row = await db.vid_get(vid)
    if row is None:
        fail(404, "VID-004", "VID inconnu")
    if row["status"] != "ACTIVE":
        fail(410, "VID-005", "VID %s" % row["status"])
    if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc):
        fail(410, "VID-006", "VID expire")
    return envelope("vidgenerator.resolve", {
        "vid": row["vid"], "uin": row["uin"], "vidType": row["vid_type"],
        "status": row["status"],
    })


@app.patch("/v1/vidgenerator/vid/{vid}")
async def vid_patch(vid: str, body: VIDPatch):
    row = await db.vid_revoke(vid, body.reason)
    if row is None:
        fail(404, "VID-004", "VID inconnu ou deja revoque")
    logger.info("VID %s revoque (%s)", vid, body.reason or "-")
    return envelope("vidgenerator.revoke", {
        "vid": row["vid"], "uin": row["uin"], "status": "REVOKED",
        "reason": row["revoked_reason"],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Reprise de l'existant (migration)
# ─────────────────────────────────────────────────────────────────────────────

class UINImport(BaseModel):
    uin: str = Field(..., pattern=r"^\d{10}$")
    note: Optional[str] = None


class VIDImport(BaseModel):
    vid: str = Field(..., pattern=r"^\d{16}$")
    uin: str = Field(..., pattern=r"^\d{10}$")
    vidType: str = Field("PERPETUAL", pattern=r"^(PERPETUAL|TEMPORARY)$")
    status: str = Field("ACTIVE", pattern=r"^(ACTIVE|REVOKED)$")
    reason: Optional[str] = None


@app.post("/v1/admin/uin/import", status_code=201)
async def uin_import(body: UINImport):
    """
    Inscrit au registre un UIN distribue avant que le registre ne tienne ce
    role. Ne genere rien : il constate. Idempotent.
    """
    if await db.uin_exists(body.uin):
        return envelope("admin.uin.import", {"uin": body.uin, "replayed": True})
    if not validate(body.uin):
        fail(400, "IDG-010", "Cle de Verhoeff invalide pour %s" % body.uin)
    await db.persist_uin(body.uin, metadata={"imported": True, "note": body.note})
    logger.info("UIN %s repris au registre (%s)", body.uin, body.note or "-")
    return envelope("admin.uin.import", {"uin": body.uin, "replayed": False})


@app.post("/v1/admin/vid/import", status_code=201)
async def vid_import(body: VIDImport):
    """
    Reprend un VID **deja emis et deja imprime sur un acte**. On ne le remplace
    pas par un neuf : un numero rendu au citoyen ne se recycle ni ne s'invalide.
    Contrairement a l'emission, l'import n'exige pas une identite ACTIVE - il
    reconstitue de l'histoire, y compris celle d'un sujet decede.
    """
    if not await db.uin_exists(body.uin):
        fail(400, "VID-010", "UIN %s inconnu du registre" % body.uin)
    existing = await db.vid_get(body.vid)
    if existing:
        if existing["uin"] != body.uin:
            fail(409, "VID-011", "VID %s deja rattache a l'UIN %s"
                 % (body.vid, existing["uin"]))
        return envelope("admin.vid.import", {
            "vid": body.vid, "uin": body.uin, "status": existing["status"],
            "replayed": True})
    row = await db.vid_persist(body.vid, body.uin, body.vidType, None)
    if body.status == "REVOKED":
        row = await db.vid_revoke(body.vid, body.reason or "import") or row
    logger.info("VID %s repris pour UIN %s (%s)", body.vid, body.uin, row["status"])
    return envelope("admin.vid.import", {
        "vid": row["vid"], "uin": row["uin"], "vidType": row["vid_type"],
        "status": row["status"], "replayed": False})
