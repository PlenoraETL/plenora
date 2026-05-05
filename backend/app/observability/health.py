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
    "check_object_storage",
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
    """Verifica che il pool PG non sia saturo: almeno 1 slot libero.

    BE-GO-READY-BOOTSTRAP-ENGINE-010: consume del helper public
    `pg_connection.get_pool_headroom()` (typed `PoolHeadroom`).
    L'accesso ai simboli privati `_get_pool` / `_POOL_MAX` e'
    incapsulato dentro `pg_connection`; questo modulo resta agnostico
    rispetto agli internals di psycopg2.
    """
    try:
        from app.db.pg_connection import get_pool_headroom
        headroom = get_pool_headroom()
        if headroom.saturated:
            return (
                False,
                f"pool saturo: {headroom.in_use}/{headroom.max} in uso, "
                f"{headroom.free} free",
            )
        return (
            True,
            f"{headroom.in_use}/{headroom.max} in uso, {headroom.free} free",
        )
    except Exception as e:
        # Se non riusciamo a ispezionare, non falliamo /ready —
        # un SELECT 1 e' gia' successo sopra. Behavior pre-batch-010
        # preservato byte-per-byte.
        return True, f"introspection skipped: {type(e).__name__}"


def check_object_storage() -> CheckOutcome:
    """Verifica raggiungibilita' object storage runtime.

    BE-GO-READY-BOOTSTRAP-ENGINE-011-OBSERVABILITY-OBJECT-STORAGE-HEALTH:
    consumer dei boundary capability + factory esistenti in
    `app.shared.cloud_backends`. NON importa boto3 / S3 SDK
    direttamente; la SDK e' incapsulata dentro `cloud_backends`.

    Strategia (in ordine):
    1. Se `object_storage_enabled()` e' False (policy:
       `STORAGE_POLICY=db` o capability disabilitata) -> outcome
       `(True, "skipped: capability disabled")`. Modalita'
       on-prem/local senza object storage e' configurazione
       legittima, NON deve far fallire /ready.
    2. Altrimenti,
       `get_runtime_object_storage().list_objects(prefix=".plenora-readiness-probe/")`
       con un dry-run leggero su prefisso dedicato normalmente vuoto.
       Bucket assente, credenziali scadute, network unreachable ->
       exception -> outcome `(False, "<ExcType>: <msg>")`.
    3. `ObjectStorageDisabledError` race (capability ON al check
       sopra, OFF al call) -> outcome `(True, "skipped: capability
       disabled (race)")`.

    Esposto in `gather_readiness` come quinto check `object_storage`.
    NON e' integrato in `gather_liveness` per scelta:  /health resta
    "load balancer-friendly" e non deve dipendere da provider di
    storage cloud (latenza variabile, regional outages,
    credentials).
    """
    try:
        from app.shared.cloud_backends import (
            ObjectStorageDisabledError,
            get_runtime_object_storage,
            object_storage_enabled,
        )
    except Exception as e:
        # Import failure (modulo rotto / cycle): treat as fail-OPEN
        # diagnostic, parallelo a `check_pool_headroom` /
        # `check_migrations_aligned`. Un broken import del modulo
        # cloud_backends sarebbe gia' stato catturato dal boot
        # validate (`validate_storage_config`) o dai test.
        return True, f"check skipped: {type(e).__name__}"

    if not object_storage_enabled():
        # Modalita' DB-only / on-prem: capability disabilitata e'
        # configurazione legittima, non un errore.
        return True, "skipped: capability disabled"

    try:
        storage = get_runtime_object_storage()
    except ObjectStorageDisabledError:
        # Race teorica: capability ON al check, OFF qui.
        return True, "skipped: capability disabled (race)"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    try:
        # Dry-run lookup su prefisso dedicato normalmente vuoto. Non
        # usiamo prefix="" perche' `cloud_backends.list_objects`
        # ritorna una lista materializzata e su bucket grandi
        # scorrerebbe tutte le pagine.
        storage.list_objects(prefix=".plenora-readiness-probe/")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    return True, "ok"


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
    """Compose /ready body. Readiness = liveness + pool_headroom +
    migrations_aligned + object_storage.

    Status: 200 se tutti e 5 i check ok (o `object_storage` skipped
    by capability), 503 altrimenti. Body shape:
    `{"status": "ready"|"not_ready", "checks": {...5 keys...}}`.

    BE-GO-READY-BOOTSTRAP-ENGINE-011: aggiunto check `object_storage`
    come additivo. `/health` (gather_liveness) NON include questo
    check — preserva la separazione load-balancer (latenza bounded)
    vs readiness rigorosa.
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

    obj_ok, obj_detail = check_object_storage()
    checks["object_storage"] = {"ok": obj_ok, "detail": obj_detail}
    if not obj_ok:
        status_code = 503

    body = {
        "status": "ready" if status_code == 200 else "not_ready",
        "checks": checks,
    }
    return HealthPayload(status_code=status_code, body=body)
