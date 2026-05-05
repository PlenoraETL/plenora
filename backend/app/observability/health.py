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
    "check_reconciliation_backlog",
    "check_bootstrap_complete",
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


def check_reconciliation_backlog() -> CheckOutcome:
    """Verifica che il backlog di `plenora.storage_reconciliation_log`
    non abbia accumulato troppi `pending` o `lost`.

    BE-GO-READY-STORAGE-ENGINE-015-RECONCILIATION-READINESS-CHECK:
    consumer di `count_active_by_state_domain()` (gia' bounded da
    SQL filter `state IN ('pending','lost')`). Threshold-based:
      - `pending_total > STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD`
        oppure
      - `lost_total > STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD`
    -> outcome `(False, "<state>=<count> over <thr>: ...")`.

    Cap <= 0 su un threshold disabilita SOLO quel branch (escape
    hatch ops). Se entrambi disabilitati il check resta sempre OK
    informando comunque `pending=N lost=M`.

    Errori di lettura (`count_active_by_state_domain` swallow → `[]`)
    sono trattati come fail-OPEN: outcome `(True, "skipped: ...")`,
    paralleloamente a `check_pool_headroom` / `check_migrations_aligned`.
    Il summary log nel beat hook resta la fonte primaria di
    diagnostic per backlog.

    Esposto in `gather_readiness` come 6° check
    `reconciliation_backlog`. NON e' integrato in `gather_liveness`:
    /health resta load-balancer-friendly e non deve dipendere dal
    DB-side state della reconciliation.
    """
    try:
        from app.shared.storage_reconciliation import (
            count_active_by_state_domain,
        )
    except Exception as e:
        # Modulo rotto / cycle: fail-OPEN diagnostic.
        return True, f"check skipped: {type(e).__name__}"

    try:
        from app.config import settings
        pending_threshold = int(
            settings.get("STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD", 1000)
        )
        lost_threshold = int(
            settings.get("STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD", 10)
        )
    except Exception as e:
        return True, f"check skipped: {type(e).__name__}"

    try:
        counts = count_active_by_state_domain()
    except Exception as e:
        # `count_active_by_state_domain` gia' swallow internamente,
        # ma defense-in-depth.
        return True, f"check skipped: {type(e).__name__}"

    pending_total = sum(c.count for c in counts if c.state == "pending")
    lost_total = sum(c.count for c in counts if c.state == "lost")

    over = []
    if pending_threshold > 0 and pending_total > pending_threshold:
        over.append(f"pending={pending_total} over {pending_threshold}")
    if lost_threshold > 0 and lost_total > lost_threshold:
        over.append(f"lost={lost_total} over {lost_threshold}")

    detail = f"pending={pending_total} lost={lost_total}"
    if over:
        return False, f"{'; '.join(over)} ({detail})"
    return True, detail


def check_bootstrap_complete() -> CheckOutcome:
    """Verifica che il boundary canonico `app.bootstrap.bootstrap()`
    sia stato attraversato dal processo web.

    BE-GO-READY-BOOTSTRAP-ENGINE-015-READY-CONSUMES-IS-BOOTSTRAPPED:
    consumer del flag tipato `app.bootstrap.is_bootstrapped()`
    introdotto nel batch 026 (BOOTSTRAP-ENGINE-013). Il flag flippa a
    True solo dopo che i 4 step canonici (`validate_startup`,
    `validate_storage_config`, `init_all_dbs`,
    `_repair_psycopg2_ssl_env`) sono completati senza eccezioni.

    Outcome:
      - True quando `is_bootstrapped()` ritorna True;
      - False quando `is_bootstrapped()` ritorna False (slow-boot
        window, partial bootstrap dopo crash mid-step, oppure boot
        non ancora avvenuto in setup CLI/test che importano `app`
        senza chiamare `bootstrap()`).

    Rationale del fail-fast esplicito:
      Senza questo check, durante la finestra "FastAPI app esiste,
      uvicorn ascolta, ma `bootstrap()` sta ancora eseguendo lo
      step 3 (`init_all_dbs` con migration pesanti)" il `/ready`
      restituirebbe 503 perche' i singoli check (PG / Redis /
      migrations) gia' falliscono — ma il messaggio sarebbe meno
      chiaro. Il check `bootstrap_complete` rende esplicito alla
      readiness probe (k8s / load balancer) che il problema e' a
      livello di boundary, non di una singola dipendenza. Il check
      resta additivo: `gather_readiness` continua a comporre anche
      gli altri check diagnostici nello stesso payload.

    Boundary contract:
      - NON tocca `gather_liveness`. Liveness rimane "il processo
        risponde" (PG SELECT 1 + Redis PING). La completion del
        bootstrap e' una nozione di readiness, non di liveness:
        un k8s liveness probe che fallisce uccide il pod, ma il
        pod che sta bootstrappando NON va ucciso, va solo escluso
        dal load balancing.
      - Esposto in `gather_readiness` come 7° check
        `bootstrap_complete`.
      - Fail-CLOSED: a differenza dei diagnostic check
        (`check_pool_headroom` / `check_migrations_aligned` /
        `check_object_storage` import-error / `check_reconciliation_backlog`),
        un import-error qui significa "il modulo bootstrap e'
        rotto" → /ready deve fallire (non vogliamo rispondere
        "ready" su un app che non ha nemmeno un boundary di
        bootstrap valido).

    Test harness considerations:
      Il conftest in `tests2/conftest.py` no-op `bootstrap()`. I
      test che esercitano `gather_readiness` end-to-end devono
      monkeypatchare `app.bootstrap.is_bootstrapped` o `_BOOT_COMPLETE`
      al valore desiderato — gli stessi test esistenti per gli
      altri 6 check usano `patch.object(h, "check_*", ...)` come
      escape hatch.
    """
    try:
        from app.bootstrap import is_bootstrapped
    except Exception as e:
        # Import error: il modulo bootstrap e' rotto. Fail-CLOSED
        # — non possiamo confermare un bootstrap che non sappiamo
        # nemmeno verificare.
        return False, f"{type(e).__name__}: {e}"

    if is_bootstrapped():
        return True, "ok"
    return False, "bootstrap not complete"


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
    migrations_aligned + object_storage + reconciliation_backlog +
    bootstrap_complete.

    Status: 200 se tutti e 7 i check ok (o `object_storage` skipped
    by capability), 503 altrimenti. Body shape:
    `{"status": "ready"|"not_ready", "checks": {...7 keys...}}`.

    BE-GO-READY-BOOTSTRAP-ENGINE-011: aggiunto check `object_storage`
    come additivo.
    BE-GO-READY-STORAGE-ENGINE-015: aggiunto `reconciliation_backlog`.
    BE-GO-READY-BOOTSTRAP-ENGINE-015: aggiunto `bootstrap_complete`
    come additivo. Liveness (`gather_liveness`) NON include nessuno
    di questi 5 check addizionali — preserva la separazione
    load-balancer (latenza bounded) vs readiness rigorosa.
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

    backlog_ok, backlog_detail = check_reconciliation_backlog()
    checks["reconciliation_backlog"] = {"ok": backlog_ok, "detail": backlog_detail}
    if not backlog_ok:
        status_code = 503

    boot_ok, boot_detail = check_bootstrap_complete()
    checks["bootstrap_complete"] = {"ok": boot_ok, "detail": boot_detail}
    if not boot_ok:
        status_code = 503

    body = {
        "status": "ready" if status_code == 200 else "not_ready",
        "checks": checks,
    }
    return HealthPayload(status_code=status_code, body=body)
