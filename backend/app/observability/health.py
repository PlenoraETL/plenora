"""
Health / readiness boundary.

"""

from __future__ import annotations

from datetime import datetime, timezone

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
    "check_beat_heartbeat",
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


def check_postgres() -> CheckOutcome:
    """SELECT 1 sul pool PG con statement_timeout bounded.

     il timeout e'
    applicato a livello di statement via `SET LOCAL statement_timeout`.
    Senza questo bound, una PG che accetta la connessione TCP ma stalla
    nell'esecuzione (lock, backend esauriti, query plan stuck) farebbe
    bloccare /ready oltre il timeout di rete del kernel.

    Il `SET LOCAL` ha scope sulla transaction in corso e viene
    automaticamente revocato quando la transazione termina (commit o
    rollback). Non altera il `statement_timeout` di altre query del
    pool: il bound resta confinato al readiness probe.

    Timeout letto da `READINESS_PG_TIMEOUT_SECONDS` (default 2). Cap
    <= 0 ricade sul fallback hardcoded di 2s (non disabilitabile:
    /ready senza bound sarebbe un footgun ops, stessa policy di
    `READINESS_REDIS_TIMEOUT_SECONDS`).

    Return (ok, detail). Fail-CLOSED su exception: PG e' dipendenza
    obbligatoria, niente fail-OPEN come i diagnostic checks.
    """
    try:
        from app.db.pg_connection import get_connection, put_connection
        from app.config import settings
        try:
            timeout = int(settings.get("READINESS_PG_TIMEOUT_SECONDS", 2))
        except Exception:
            timeout = 2
        if timeout <= 0:
            timeout = 2
        timeout_ms = timeout * 1000
        # Concatenation keeps this visible to the static SQL sentinel; the
        # interpolated value is an int derived from bounded settings.
        set_local_sql = "SET LOCAL statement_timeout = " + str(timeout_ms)
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(set_local_sql)
                cur.execute(
                    "SELECT 1 AS v, current_schema() AS current_schema"
                )
                row = cur.fetchone()
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            put_connection(conn)
        if row and int(row.get("v", 0)) == 1:
            expected_schema = settings.get("DB_SCHEMA", "plenora")
            current_schema = row.get("current_schema")
            if current_schema != expected_schema:
                return (
                    False,
                    f"schema mismatch: current={current_schema!r} "
                    f"expected={expected_schema!r}",
                )
            return True, f"ok schema={current_schema}"
        return False, f"SELECT 1 ha restituito: {row!r}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_redis() -> CheckOutcome:
    """PING su Redis broker Celery.

     il timeout e'
    applicato sia al TCP handshake (`socket_connect_timeout`) sia
    all'operazione PING (`socket_timeout`). Senza socket_timeout
    una Redis che accetta la connessione ma stalla bloccherebbe
    /ready oltre il connect_timeout (worst-case TCP keepalive
    finch* il kernel non droppa la socket).

    Timeout letto da `READINESS_REDIS_TIMEOUT_SECONDS` (default 2);
    un valore <= 0 ricade sul fallback hardcoded 2 (non
    disabilitabile: /ready senza bound sarebbe un footgun ops).
    """
    try:
        from app.config import settings
        from app.observability.runtime_ports import (
            bounded_readiness_timeout,
            ping_redis_broker,
        )
        timeout = bounded_readiness_timeout(
            settings,
            "READINESS_REDIS_TIMEOUT_SECONDS",
            2,
        )
        if ping_redis_broker(settings.CELERY_BROKER_URL, timeout=timeout):
            return True, "ok"
        return False, "PING non ha risposto True"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_migrations_aligned() -> CheckOutcome:
    """Readiness check for deterministic migration alignment.

    Confronta files in migrations/ con righe in schema_migrations.
    503 se la DB e' empty/pending/misaligned. Questo rende visibili
    sia il primo bootstrap non ancora completato sia deploy a meta'
    o tracking rows non piu' corrispondenti ai file su disco.
    """
    try:
        from app.db.migrations_runner import get_migration_status
        status = get_migration_status()
        if status.state == "migrated":
            return True, "aligned"
        if status.state == "empty":
            return (
                False,
                "empty database: "
                f"{len(status.pending_migrations)} pending: "
                f"{', '.join(status.pending_migrations[:3])}",
            )
        if status.state == "pending":
            return (
                False,
                f"{len(status.pending_migrations)} pending: "
                f"{', '.join(status.pending_migrations[:3])}",
            )
        if status.state == "misaligned":
            return (
                False,
                "misaligned: "
                f"{len(status.unknown_applied_migrations)} unknown applied: "
                f"{', '.join(status.unknown_applied_migrations[:3])}",
            )
        return False, f"unknown migration state: {status.state}"
    except Exception as e:
        # Non falliamo /ready per un problema di introspection stessa —
        # un check PG/Redis a monte ha gia' validato la raggiungibilita'
        return True, f"check skipped: {type(e).__name__}"


def check_pool_headroom() -> CheckOutcome:
    """Verifica che il pool PG non sia saturo: almeno 1 slot libero.

     consume del helper public
    `pg_connection.get_pool_headroom` (typed `PoolHeadroom`).
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
        # This is diagnostic only; the upstream SELECT 1 already validated PG.
        return True, f"introspection skipped: {type(e).__name__}"


def check_object_storage() -> CheckOutcome:
    """Verifica raggiungibilita' object storage runtime.

    consumer dei boundary capability + factory esistenti in
    `app.infrastructure.storage.cloud_backends`. NON importa boto3 / S3 SDK
    direttamente; la SDK e' incapsulata dentro `cloud_backends`.

    Strategia (in ordine):
    1. Se `object_storage_enabled` e' False (policy:
       `STORAGE_POLICY=db` o capability disabilitata) -> outcome
       `(True, "skipped: capability disabled")`. Modalita'
       on-prem/local senza object storage e' configurazione
       legittima, NON deve far fallire /ready.
    2. Altrimenti,
       `get_runtime_object_storage.list_objects(prefix=".plenora-readiness-probe/")`
       con un dry-run leggero su prefisso dedicato normalmente vuoto.
       Bucket assente, credenziali scadute, network unreachable ->
       exception -> outcome `(False, "<ExcType>: <msg>")`.
    3. `ObjectStorageDisabledError` race (capability ON al check
       sopra, OFF al call) -> outcome `(True, "skipped: capability
       disabled (race)")`.

    Esposto in `gather_readiness` come quinto check `object_storage`.
    NON e' integrato in `gather_liveness` per scelta: /health resta
    process-only e non deve dipendere da provider di
    storage cloud (latenza variabile, regional outages,
    credentials).
    """
    try:
        from app.infrastructure.storage.cloud_backends import (
            ObjectStorageDisabledError,
            get_runtime_object_storage,
            object_storage_enabled,
        )
    except Exception as e:
        # Import failure (modulo rotto / cycle): treat as fail-OPEN
        # diagnostic, parallelo a `check_pool_headroom` /
        # `check_migrations_aligned`. Un broken import del modulo
        # cloud_backends sarebbe gia' stato catturato dal boot
        # validate (`validate_storage_config`) o dai test
        return True, f"check skipped: {type(e).__name__}"

    if not object_storage_enabled():
        # Modalita' DB-only / on-prem: capability disabilitata e'
        # configurazione legittima, non un errore
        return True, "skipped: capability disabled"

    try:
        storage = get_runtime_object_storage()
    except ObjectStorageDisabledError:
        # Race teorica: capability ON al check, OFF qui
        return True, "skipped: capability disabled (race)"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    # Keep object-storage readiness bounded; boto3 defaults can exceed the
    # load-balancer-friendly /ready window by minutes.
    from app.config import settings
    try:
        timeout = int(
            settings.get("READINESS_OBJECT_STORAGE_TIMEOUT_SECONDS", 3)
        )
    except Exception:
        timeout = 3
    if timeout <= 0:
        timeout = 3
    try:
        storage.readiness_probe(timeout_seconds=timeout)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    return True, "ok"


def check_reconciliation_backlog() -> CheckOutcome:
    """Verifica che il backlog di `plenora.storage_reconciliation_log`
    non abbia accumulato troppi `pending` o `lost`.

    consumer di `count_active_by_state_domain` (gia' bounded da
    SQL filter `state IN ('pending','lost')`). Threshold-based:
      - `pending_total > STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD`
        oppure
      - `lost_total > STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD`
    -> outcome `(False, "<state>=<count> over <thr>: ...")`.

    Include il breakdown per-operation quando disponibile e supporta
    `STORAGE_RECONCILE_BACKLOG_UPLOAD_INTENT_503_THRESHOLD` per
    segnalare separatamente backlog `upload_intent`.

    Cap <= 0 su un threshold disabilita SOLO quel branch (escape
    hatch ops). Se tutti disabilitati il check resta sempre OK
    informando comunque pending/lost + breakdown.

    Errori di lettura (`count_active_by_state_domain` swallow → `[]`)
    sono trattati come fail-OPEN: outcome `(True, "skipped: ...")`,
    paralleloamente a `check_pool_headroom` / `check_migrations_aligned`.
    Il summary log nel beat hook resta la fonte primaria di
    diagnostic per backlog.

    Esposto in `gather_readiness` come 6° check
    `reconciliation_backlog`. NON e' integrato in `gather_liveness`:
    /health resta process-only e non deve dipendere dal
    DB-side state della reconciliation.
    """
    try:
        from app.core.resources.storage_reconciliation import (
            count_active_by_state_domain,
            count_pending_by_operation,
        )
    except Exception as e:
        # Modulo rotto / cycle: fail-OPEN diagnostic
        return True, f"check skipped: {type(e).__name__}"

    try:
        from app.config import settings
        pending_threshold = int(
            settings.get("STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD", 1000)
        )
        lost_threshold = int(
            settings.get("STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD", 10)
        )
        upload_intent_threshold = int(
            settings.get(
                "STORAGE_RECONCILE_BACKLOG_UPLOAD_INTENT_503_THRESHOLD",
                0,
            )
        )
    except Exception as e:
        return True, f"check skipped: {type(e).__name__}"

    try:
        counts = count_active_by_state_domain()
    except Exception as e:
        # `count_active_by_state_domain` gia' swallow internamente
        # ma defense-in-depth
        return True, f"check skipped: {type(e).__name__}"

    # Per-operation breakdown: best-effort, errors degrade to empty
    # dict (no per-operation visibility but the global
    # state-domain numbers still drive the 503 logic
    try:
        per_op = count_pending_by_operation()
    except Exception:
        per_op = {}

    pending_total = sum(c.count for c in counts if c.state == "pending")
    lost_total = sum(c.count for c in counts if c.state == "lost")

    over = []
    if pending_threshold > 0 and pending_total > pending_threshold:
        over.append(f"pending={pending_total} over {pending_threshold}")
    if lost_threshold > 0 and lost_total > lost_threshold:
        over.append(f"lost={lost_total} over {lost_threshold}")

    upload_intent_pending = int(per_op.get("upload_intent", 0))
    if (
        upload_intent_threshold > 0
        and upload_intent_pending > upload_intent_threshold
    ):
        over.append(
            f"upload_intent={upload_intent_pending} over {upload_intent_threshold}"
        )

    detail = f"pending={pending_total} lost={lost_total}"
    if per_op:
        breakdown = " ".join(f"{k}={per_op[k]}" for k in sorted(per_op.keys()))
        detail = f"pending={pending_total} ({breakdown}) lost={lost_total}"
    if over:
        return False, f"{'; '.join(over)} ({detail})"
    return True, detail


def check_beat_heartbeat() -> CheckOutcome:
    """Verifica che il beat scheduler abbia scritto un heartbeat
    recente in `plenora.beat_heartbeat`.

    Lo heartbeat e' scritto ad ogni tick da
    `PlenoraBeatScheduler._write_heartbeat` (INSERT ... ON
    CONFLICT su `plenora.beat_heartbeat WHERE id='beat'`); questo
    helper lo legge e verifica che `now - ts` non superi un
    threshold.

    Threshold-based opt-in:
      - `READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS <= 0` (default)
        → outcome `(True, "skipped: disabled (threshold=0)")`.
        DEV/test sicuro: senza beat scheduler attivo
        (test runner, CLI, smoke environment) il check non
        triggera mai 503;
      - `> 0` → outcome `(False, "stale beat: ...")` se l'eta'
        dell'heartbeat supera il threshold; `(False, "missing
        heartbeat: ...")` se la row non esiste; `(True, ...)`
        altrimenti.

    Failure detail wording (locked dai test):
      - row missing: `"missing heartbeat (table empty)"`;
      - row presente ma stale: `"stale beat: age=Ns max=Ts"`;
      - row presente ma `ts` malformato: fail-OPEN
        `"check skipped: ..."`.

    Errori di lettura (DB unreachable / table missing in dev):
    fail-OPEN `(True, "check skipped: <ExcType>")`. Lo stesso
    pattern di `check_pool_headroom` / `check_migrations_aligned`
    / `check_reconciliation_backlog`: una probe diagnostic non
    deve far fallire /ready se il segnale stesso e' degradato.

    Esposto in `gather_readiness` come 8° check `beat_heartbeat`.
    NON in `gather_liveness`: /health resta process-only
    e non deve dipendere dallo stato del beat scheduler.
    """
    try:
        from app.config import settings
        max_age = int(
            settings.get("READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS", 0)
        )
    except Exception as e:
        return True, f"check skipped: {type(e).__name__}"

    if max_age <= 0:
        # Default disabled: dev / test / CLI environments without a
        # running beat process never trigger 503. Operators opt in
        # via env override
        return True, "skipped: disabled (threshold=0)"

    try:
        from app.db.pg_connection import unit_of_work
        with unit_of_work() as session:
            row = session.fetchone(
                "SELECT ts FROM beat_heartbeat WHERE id = 'beat'"
            )
    except Exception as e:
        # Table missing (fresh dev DB) / DB unreachable / SQL syntax
        # error: fail-OPEN diagnostic. The PG check upstream already
        # validates DB reachability
        return True, f"check skipped: {type(e).__name__}"

    if not row or not row.get("ts"):
        return False, "missing heartbeat (table empty)"

    try:
        ts_raw = row["ts"]
        if isinstance(ts_raw, str):
            beat_time = datetime.fromisoformat(ts_raw)
        else:
            beat_time = ts_raw
        if beat_time.tzinfo is None:
            beat_time = beat_time.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - beat_time).total_seconds()
    except Exception as e:
        # Malformed timestamp: fail-OPEN. A consistent ts shape would
        # have come from `_write_heartbeat` which uses isoformat
        # anything else is a corruption that the readiness probe
        # cannot diagnose
        return True, f"check skipped: {type(e).__name__}"

    if age_seconds > max_age:
        return (
            False,
            f"stale beat: age={int(age_seconds)}s max={max_age}s",
        )
    return True, f"age={int(age_seconds)}s max={max_age}s"


def check_bootstrap_complete() -> CheckOutcome:
    """Verifica che il boundary canonico `app.bootstrap.bootstrap()`
    sia stato attraversato dal processo web.

    consumer del flag tipato `app.bootstrap.is_bootstrapped`. Il flag
    flippa a True solo dopo che i 4 step canonici (`validate_startup`,
    `validate_storage_config`, `init_all_dbs`,
    `_repair_psycopg2_ssl_env`) sono completati senza eccezioni.

    Outcome:
      - True quando `is_bootstrapped` ritorna True;
      - False quando `is_bootstrapped` ritorna False (slow-boot
        window, partial bootstrap dopo crash mid-step, oppure boot
        non ancora avvenuto in setup CLI/test che importano `app`
        senza chiamare `bootstrap`).

    Rationale del fail-fast esplicito:
      Senza questo check, durante la finestra "FastAPI app esiste,
      uvicorn ascolta, ma `bootstrap` sta ancora eseguendo lo
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
      Il conftest in `tests2/conftest.py` no-op `bootstrap`. I
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
        # nemmeno verificare
        return False, f"{type(e).__name__}: {e}"

    if is_bootstrapped():
        return True, "ok"
    return False, "bootstrap not complete"


def gather_liveness() -> HealthPayload:
    """Compose /health body. Liveness = process responds.

    Status: always 200 when the FastAPI process can serve the
    request. Dependency lag belongs to /ready, because a liveness
    failure can restart the app while a readiness failure only removes
    it from load balancing.
    """
    body = {
        "status": "ok",
        "checks": {
            "process": {"ok": True, "detail": "alive"},
        },
    }
    return HealthPayload(status_code=200, body=body)


def gather_readiness() -> HealthPayload:
    """Compose /ready body. Readiness = dependency probes + pool_headroom +
    migrations_aligned + object_storage + reconciliation_backlog +
    beat_heartbeat + bootstrap_complete.

    Status: 200 se tutti gli 8 check sono ok (o skipped da
    capability/threshold), 503 altrimenti. Body shape:
    `{"status": "ready"|"not_ready", "checks": {...8 keys...}}`.

    Default opt-in disabled (`READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS=0`)
    cosi' dev/test senza beat scheduler attivo NON triggerano 503.

    Liveness (`gather_liveness`) NON include questi check: /health
    resta process-only, mentre /ready espone i deep dependency probes
    bounded (PG SELECT 1 + Redis PING) e i diagnostic checks.
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

    beat_ok, beat_detail = check_beat_heartbeat()
    checks["beat_heartbeat"] = {"ok": beat_ok, "detail": beat_detail}
    if not beat_ok:
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
