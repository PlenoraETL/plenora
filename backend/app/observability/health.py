"""Health / readiness boundary.

BE-GO-READY-BOOTSTRAP-ENGINE-009-HEALTH-EXTRACTION.

Estratto verbatim dai 4 helper `_check_*` che vivevano in
`backend/app/main.py:502-559`. Behavior user-visible invariato:
stessi tuple `(ok, detail)`, stessa exception classification
("<ExcType>: <msg>"), stessi fail-OPEN fallback per i due helper
diagnostic (pool headroom + migrations aligned).

Boundary
--------
Leaf module: NO import di FastAPI / Starlette / boto3. Lazy import
verso `app.db.pg_connection`, `app.db.migrations_runner`, `redis`
(SDK) per preservare il pattern del codice originale (no top-level
side effect su un dependency che potrebbe essere assente in
contesti CLI/test).

Settings consumato via attribute read su `app.config.settings`
(singleton ADR-0021); zero `os.getenv`.

Architettura
------------
4 funzioni `check_*` ritornano `CheckOutcome = tuple[bool, str]`.
2 funzioni `gather_*` compongono il payload finale e ritornano un
`HealthPayload` typed (frozen dataclass: `status_code` + `body`)
che le route in `main.py` consegnano a `JSONResponse`.

Le route restano nei loro decoratori FastAPI in `main.py` per
preservare il routing wiring; questo modulo non sa che FastAPI
esiste.

Go-ready mapping
----------------
::

    type CheckOutcome struct { OK bool; Detail string }
    type HealthPayload struct { StatusCode int; Body map[string]any }

    func CheckPostgres() CheckOutcome
    func CheckRedis() CheckOutcome
    func CheckPoolHeadroom() CheckOutcome
    func CheckMigrationsAligned() CheckOutcome
    func GatherLiveness() HealthPayload
    func GatherReadiness() HealthPayload
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


CheckOutcome = Tuple[bool, str]


__all__ = [
    "CheckOutcome",
    "HealthPayload",
    "check_postgres",
    "check_redis",
    "check_pool_headroom",
    "check_migrations_aligned",
    "gather_liveness",
    "gather_readiness",
]


@dataclass(frozen=True)
class HealthPayload:
    """Payload tipato per /health e /ready.

    `status_code` e' 200 (ok/ready) o 503 (unhealthy/not_ready).
    `body` e' un dict shape `{"status": "ok"|"unhealthy"|"ready"|"not_ready",
    "checks": {"<name>": {"ok": bool, "detail": str}, ...}}`.
    """

    status_code: int
    body: dict


# ---------------------------------------------------------------------------
# Single dependency checks — preservato verbatim da main.py:502-559
# ---------------------------------------------------------------------------


def check_postgres() -> CheckOutcome:
    """SELECT 1 sul pool PG. Return (ok, detail)."""
    try:
        from app.db.pg_connection import fetchone
        row = fetchone("SELECT 1 AS v")
        if row and int(row.get("v", 0)) == 1:
            return True, "ok"
        return False, f"SELECT 1 ha restituito: {row!r}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_redis() -> CheckOutcome:
    """PING su Redis broker Celery."""
    try:
        import redis
        from app.config import settings
        r = redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2)
        if r.ping():
            return True, "ok"
        return False, "PING non ha risposto True"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_migrations_aligned() -> CheckOutcome:
    """C8-nit: /ready rigoroso include check "migrations allineate".
    Confronta files in migrations/ con righe in schema_migrations.
    503 se ci sono pending (deploy a meta' / file committato ma non
    ancora applicato dal runner)."""
    try:
        from app.db.migrations_runner import list_pending_migrations
        pending = list_pending_migrations()
        if pending:
            return False, f"{len(pending)} pending: {', '.join(pending[:3])}"
        return True, "aligned"
    except Exception as e:
        # Non falliamo /ready per un problema di introspection stessa —
        # un check PG/Redis a monte ha gia' validato la raggiungibilita'.
        return True, f"check skipped: {type(e).__name__}"


def check_pool_headroom() -> CheckOutcome:
    """Verifica che il pool PG non sia saturo: almeno 1 slot libero."""
    try:
        # NEEDS_CONTEXT batch 010 (vedi
        # `.agent-temp/BE_BOOTSTRAP_ENGINE_AUDIT.md` §8.6 R-008.1):
        # `_get_pool` e `_POOL_MAX` sono simboli privati di
        # `pg_connection`. Li riusiamo as-is per behavior preservation
        # (best-effort diagnostic). La promozione a public helper
        # `pg_connection.get_pool_headroom()` e' tracciata in batch 010.
        from app.db.pg_connection import _get_pool, _POOL_MAX
        pool = _get_pool()
        # psycopg2.pool._used e' lista conn in uso, _pool e' lista free.
        # Accesso interno — best-effort diagnostic.
        in_use = len(getattr(pool, "_used", {}) or {})
        free = len(getattr(pool, "_pool", []) or [])
        used_total = in_use
        if used_total >= _POOL_MAX:
            return False, f"pool saturo: {used_total}/{_POOL_MAX} in uso, {free} free"
        return True, f"{used_total}/{_POOL_MAX} in uso, {free} free"
    except Exception as e:
        # Se non riusciamo a ispezionare, non falliamo /ready —
        # un SELECT 1 e' gia' successo sopra.
        return True, f"introspection skipped: {type(e).__name__}"


# ---------------------------------------------------------------------------
# Payload composition — gather_liveness / gather_readiness
# ---------------------------------------------------------------------------


def gather_liveness() -> HealthPayload:
    """Compose /health body. Liveness = PG SELECT 1 + Redis PING.

    Status: 200 se entrambi rispondono, 503 altrimenti. Body shape:
    `{"status": "ok"|"unhealthy", "checks": {"postgres": {...}, "redis": {...}}}`.
    """
    checks: dict = {}
    status_code = 200

    pg_ok, pg_detail = check_postgres()
    checks["postgres"] = {"ok": pg_ok, "detail": pg_detail}
    if not pg_ok:
        status_code = 503

    redis_ok, redis_detail = check_redis()
    checks["redis"] = {"ok": redis_ok, "detail": redis_detail}
    if not redis_ok:
        status_code = 503

    body = {
        "status": "ok" if status_code == 200 else "unhealthy",
        "checks": checks,
    }
    return HealthPayload(status_code=status_code, body=body)


def gather_readiness() -> HealthPayload:
    """Compose /ready body. Readiness = liveness + pool_headroom + migrations_aligned.

    Status: 200 se tutti e 4 i check ok, 503 altrimenti. Body shape:
    `{"status": "ready"|"not_ready", "checks": {...4 keys...}}`.
    """
    checks: dict = {}
    status_code = 200

    pg_ok, pg_detail = check_postgres()
    checks["postgres"] = {"ok": pg_ok, "detail": pg_detail}
    if not pg_ok:
        status_code = 503

    redis_ok, redis_detail = check_redis()
    checks["redis"] = {"ok": redis_ok, "detail": redis_detail}
    if not redis_ok:
        status_code = 503

    pool_ok, pool_detail = check_pool_headroom()
    checks["pool_headroom"] = {"ok": pool_ok, "detail": pool_detail}
    if not pool_ok:
        status_code = 503

    mig_ok, mig_detail = check_migrations_aligned()
    checks["migrations"] = {"ok": mig_ok, "detail": mig_detail}
    if not mig_ok:
        status_code = 503

    body = {
        "status": "ready" if status_code == 200 else "not_ready",
        "checks": checks,
    }
    return HealthPayload(status_code=status_code, body=body)
