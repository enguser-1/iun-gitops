"""
PostgreSQL access layer - connects to CNPG cluster mosip-postgres,
database mosip_kernel (already initialized via postgres-init fix #M2).

v2.0 (2026-08-20) - le service devient le registre souverain d'identite :
  - iun_uin_issued      : les numeros distribues (v1, inchangee)
  - iun_uin_reservation : journal d'allocation, idempotence par evenement
  - iun_identity        : l'identite derriere le numero, et son statut
  - iun_vid             : les VID emis, revocables et rotatifs

Uses asyncpg for connection pooling. Credentials from env (Secret-backed).
"""
import os
import json
import unicodedata
import asyncpg
from typing import Optional, List, Dict, Any

DB_HOST = os.getenv("DB_HOST", "mosip-postgres-rw.iun-mosip-dev.svc.cluster.local")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "mosip_kernel")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

_pool: Optional[asyncpg.Pool] = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    """asyncpg rend le JSONB en chaine par defaut : on decode a la lecture,
    sinon `identity` remonte comme du texte jusque dans les reponses HTTP."""
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(
            typename,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


async def get_pool() -> asyncpg.Pool:
    """Lazy-initialize the asyncpg connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            init=_init_conn,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            min_size=2,
            max_size=10,
            command_timeout=10,
        )
    return _pool


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS iun_uin_issued (
    id BIGSERIAL PRIMARY KEY,
    uin VARCHAR(10) UNIQUE NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    metadata JSONB
);
CREATE INDEX IF NOT EXISTS idx_iun_uin_issued_status ON iun_uin_issued(status);
CREATE INDEX IF NOT EXISTS idx_iun_uin_issued_issued_at ON iun_uin_issued(issued_at);

-- Journal d'allocation : un numero frappe puis perdu devient impossible,
-- et deux chemins d'attribution concurrents ne peuvent plus frapper deux fois
-- pour le meme evenement (cle d'idempotence = identifiant d'evenement).
CREATE TABLE IF NOT EXISTS iun_uin_reservation (
    reservation_id  UUID PRIMARY KEY,
    uin             VARCHAR(10) NOT NULL,
    idempotency_key VARCHAR(160) UNIQUE,
    state           VARCHAR(16) NOT NULL DEFAULT 'RESERVED',
    requester       VARCHAR(64),
    reserved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at      TIMESTAMPTZ,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_iun_reservation_state ON iun_uin_reservation(state);
CREATE INDEX IF NOT EXISTS idx_iun_reservation_uin   ON iun_uin_reservation(uin);

-- L'identite derriere le numero. Sans cette table, MOSIP a distribue un
-- nombre sans savoir a qui : ni resolution, ni dedoublonnage, ni cycle de vie.
CREATE TABLE IF NOT EXISTS iun_identity (
    uin             VARCHAR(10) PRIMARY KEY,
    status          VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    merged_into     VARCHAR(10),
    identity        JSONB NOT NULL,
    registration_id VARCHAR(64),
    source          VARCHAR(32) NOT NULL DEFAULT 'opencrvs',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deactivated_at  TIMESTAMPTZ,
    deactivation_reason VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_iun_identity_status ON iun_identity(status);
CREATE INDEX IF NOT EXISTS idx_iun_identity_nin  ON iun_identity ((identity->>'nationalId'));
CREATE INDEX IF NOT EXISTS idx_iun_identity_cni  ON iun_identity ((identity->>'cniNumber'));
CREATE INDEX IF NOT EXISTS idx_iun_identity_brn  ON iun_identity ((identity->>'birthRegistrationNumber'));
CREATE INDEX IF NOT EXISTS idx_iun_identity_dob  ON iun_identity ((identity->>'dateOfBirth'));
CREATE INDEX IF NOT EXISTS idx_iun_identity_name ON iun_identity ((identity->>'nameKey'));

-- Les VID : emis ici, donc reellement revocables et rotatifs.
CREATE TABLE IF NOT EXISTS iun_vid (
    vid            VARCHAR(16) PRIMARY KEY,
    uin            VARCHAR(10) NOT NULL,
    vid_type       VARCHAR(16) NOT NULL DEFAULT 'PERPETUAL',
    status         VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at     TIMESTAMPTZ,
    revoked_at     TIMESTAMPTZ,
    revoked_reason VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_iun_vid_uin    ON iun_vid(uin, status);
CREATE INDEX IF NOT EXISTS idx_iun_vid_status ON iun_vid(status);
"""


async def init_schema() -> None:
    """Idempotent - safe to run on every pod startup."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


# ─────────────────────────────────────────────────────────────────────────────
# UIN (v1, inchange)
# ─────────────────────────────────────────────────────────────────────────────

async def persist_uin(uin: str, metadata: Optional[dict] = None) -> int:
    """
    Insert a UIN into the registry. Raises asyncpg.UniqueViolationError
    if uin already exists - caller should handle race conditions.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO iun_uin_issued (uin, metadata) VALUES ($1, $2) RETURNING id",
            uin,
            metadata if metadata else None,
        )
        return row["id"]


async def uin_exists(uin: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM iun_uin_issued WHERE uin = $1 LIMIT 1", uin
        )
        return row is not None


async def count_issued() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM iun_uin_issued") or 0


async def health_check() -> bool:
    """Returns True if the DB is reachable and query works."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:
        return False


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ─────────────────────────────────────────────────────────────────────────────
# Journal d'allocation
# ─────────────────────────────────────────────────────────────────────────────

async def reservation_by_key(key: str) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM iun_uin_reservation WHERE idempotency_key = $1", key
        )
        return dict(row) if row else None


async def reserve_uin(reservation_id: str, uin: str, key: Optional[str],
                      requester: Optional[str]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO iun_uin_reservation
                   (reservation_id, uin, idempotency_key, requester)
               VALUES ($1::uuid, $2, $3, $4)""",
            reservation_id, uin, key, requester,
        )


async def settle_reservation(reservation_id: str, state: str,
                             note: Optional[str] = None) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        res = await conn.execute(
            """UPDATE iun_uin_reservation
                  SET state = $2, settled_at = NOW(), note = $3
                WHERE reservation_id = $1::uuid AND state = 'RESERVED'""",
            reservation_id, state, note,
        )
        return res.endswith("1")


async def stale_reservations(older_than_minutes: int = 30) -> List[Dict[str, Any]]:
    """Numeros reserves jamais confirmes : la fuite devient visible et rejouable."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM iun_uin_reservation
                WHERE state = 'RESERVED'
                  AND reserved_at < NOW() - ($1 || ' minutes')::interval
                ORDER BY reserved_at""",
            str(older_than_minutes),
        )
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Identite
# ─────────────────────────────────────────────────────────────────────────────

def name_key(full_name: Optional[str]) -> str:
    """Cle de rapprochement : sans accents, sans ponctuation, mots tries."""
    if not full_name:
        return ""
    s = unicodedata.normalize("NFD", str(full_name))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    words = sorted(w for w in "".join(c if c.isalnum() else " " for c in s).split() if w)
    return " ".join(words)


async def identity_get(uin: str) -> Optional[Dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM iun_identity WHERE uin = $1", uin)
        return dict(row) if row else None


async def identity_create(uin: str, identity: dict, registration_id: Optional[str],
                          source: str = "opencrvs") -> Dict[str, Any]:
    payload = dict(identity or {})
    payload["nameKey"] = name_key(payload.get("fullName"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO iun_identity (uin, identity, registration_id, source)
               VALUES ($1, $2::jsonb, $3, $4)
               ON CONFLICT (uin) DO UPDATE
                   SET identity = EXCLUDED.identity,
                       registration_id = COALESCE(EXCLUDED.registration_id, iun_identity.registration_id),
                       updated_at = NOW()
               RETURNING *""",
            uin, payload, registration_id, source,
        )
        return dict(row)


async def identity_update(uin: str, identity: Optional[dict]) -> Optional[Dict[str, Any]]:
    if not identity:
        return await identity_get(uin)
    payload = dict(identity)
    if "fullName" in payload:
        payload["nameKey"] = name_key(payload.get("fullName"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE iun_identity
                  SET identity = iun_identity.identity || $2::jsonb,
                      updated_at = NOW()
                WHERE uin = $1
               RETURNING *""",
            uin, payload,
        )
        return dict(row) if row else None


async def identity_set_status(uin: str, status: str,
                              reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """UPDATE iun_identity
                      SET status = $2::varchar(20),
                          updated_at = NOW(),
                          deactivated_at = CASE WHEN $2::text = 'DEACTIVATED' THEN NOW() ELSE NULL END,
                          deactivation_reason = CASE WHEN $2::text = 'DEACTIVATED' THEN $3::varchar(64) ELSE NULL END
                    WHERE uin = $1
                   RETURNING *""",
                uin, status, reason,
            )
            if row is None:
                return None
            await conn.execute(
                "UPDATE iun_uin_issued SET status = $2 WHERE uin = $1", uin, status
            )
            if status == "DEACTIVATED":
                # un identifiant d'echange ne survit pas au sujet
                await conn.execute(
                    """UPDATE iun_vid
                          SET status = 'REVOKED', revoked_at = NOW(), revoked_reason = $2
                        WHERE uin = $1 AND status = 'ACTIVE'""",
                    uin, reason or "identity_deactivated",
                )
            return dict(row)


async def identity_search(criteria: dict, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Resolution d'identite. Ne fusionne jamais, ne tranche jamais : renvoie des
    candidats classes, avec le type de correspondance. C'est a l'appelant de
    decider, et de verser en ecart ce qui reste ambigu.

    Ordre : identifiant exact d'abord, demographie ensuite.
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    pool = await get_pool()

    def add(row, match_type, score):
        if row is None:
            return
        d = dict(row)
        if d["uin"] in seen:
            return
        seen.add(d["uin"])
        d["matchType"] = match_type
        d["score"] = score
        out.append(d)

    async with pool.acquire() as conn:
        uin = (criteria.get("uin") or "").strip()
        if uin:
            add(await conn.fetchrow("SELECT * FROM iun_identity WHERE uin = $1", uin),
                "UIN", 1.0)

        vid = "".join(ch for ch in str(criteria.get("vid") or "") if ch.isdigit())
        if vid:
            add(await conn.fetchrow(
                """SELECT i.* FROM iun_identity i
                     JOIN iun_vid v ON v.uin = i.uin
                    WHERE v.vid = $1 AND v.status = 'ACTIVE'""", vid),
                "VID", 1.0)

        for field, mt in (("nationalId", "NATIONAL_ID"),
                          ("cniNumber", "CNI"),
                          ("birthRegistrationNumber", "BRN")):
            val = (criteria.get(field) or "").strip()
            if not val:
                continue
            add(await conn.fetchrow(
                "SELECT * FROM iun_identity WHERE identity->>$1 = $2", field, val),
                mt, 1.0)

        # demographie : nom normalise + date de naissance
        nk = name_key(criteria.get("fullName"))
        dob = (criteria.get("dateOfBirth") or "").strip()
        if nk and dob:
            for r in await conn.fetch(
                """SELECT * FROM iun_identity
                    WHERE identity->>'nameKey' = $1 AND identity->>'dateOfBirth' = $2
                    LIMIT $3""", nk, dob, limit):
                add(r, "DEMOGRAPHIC_STRONG", 0.85)
        elif nk and (criteria.get("yearOfBirth") or "").strip():
            yob = str(criteria.get("yearOfBirth")).strip()
            for r in await conn.fetch(
                """SELECT * FROM iun_identity
                    WHERE identity->>'nameKey' = $1
                      AND left(identity->>'dateOfBirth', 4) = $2
                    LIMIT $3""", nk, yob, limit):
                add(r, "DEMOGRAPHIC_WEAK", 0.55)

    return out[:limit]


async def count_identities() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM iun_identity") or 0


# ─────────────────────────────────────────────────────────────────────────────
# VID
# ─────────────────────────────────────────────────────────────────────────────

async def vid_persist(vid: str, uin: str, vid_type: str = "PERPETUAL",
                      expires_at=None) -> Dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO iun_vid (vid, uin, vid_type, expires_at)
               VALUES ($1, $2, $3, $4) RETURNING *""",
            vid, uin, vid_type, expires_at,
        )
        return dict(row)


async def vid_active_for(uin: str, vid_type: str = "PERPETUAL") -> Optional[Dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM iun_vid
                WHERE uin = $1 AND vid_type = $2 AND status = 'ACTIVE'
                ORDER BY created_at DESC LIMIT 1""",
            uin, vid_type,
        )
        return dict(row) if row else None


async def vid_get(vid: str) -> Optional[Dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM iun_vid WHERE vid = $1", vid)
        return dict(row) if row else None


async def vid_revoke(vid: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE iun_vid
                  SET status = 'REVOKED', revoked_at = NOW(), revoked_reason = $2
                WHERE vid = $1 AND status = 'ACTIVE'
               RETURNING *""",
            vid, reason,
        )
        return dict(row) if row else None


async def count_vids(status: str = "ACTIVE") -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM iun_vid WHERE status = $1", status) or 0
