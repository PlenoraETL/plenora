"""
Service layer — aggregatore stato sistema per dashboard admin.

Ogni metrica (backup, verify-restore, schedules, workers) e' una funzione
"""

from __future__ import annotations

import shutil as _shutil

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app import config as app_config
from app.core.admin_backups.service import list_backups
from app.core.admin_system_status.db_probe_store import (
    fetch_critical_table_rows,
    fetch_db_pool_row,
    fetch_latest_verify_run,
    fetch_schedule_rows,
    fetch_schema_size_row,
)
from app.core.admin_system_status.tickets_metrics import (
    get_active_ticket_metrics,
)
from app.core.task_runtime.probes import (
    CeleryAppUnavailable,
    RedisClientUnavailable,
    get_celery_queue_depth,
    get_redis_info,
    inspect_celery_workers,
)
from app.core.admin_system_status.models import (
    AdminStatusBlock,
    CpuStatus,
    DbSchemaTableStatus,
    DiskStatus,
    MemoryStatus,
    ScheduleFailure,
    StatusScope,
    SystemStatusPayload,
    WorkerDetail,
)
from app.core.security.token_blacklist import get_blacklist_status as _probe
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _as_status_block(
    value: AdminStatusBlock | dict[str, object],
    *,
    default_scope: StatusScope = "cluster",
) -> AdminStatusBlock:
    """Normalize test doubles and legacy delegates at the service boundary."""
    if isinstance(value, AdminStatusBlock):
        block = value
    else:
        if "message" not in value:
            value = {**value, "message": ""}
        block = AdminStatusBlock.model_validate(value)
    if block.scope is None:
        block = block.model_copy(update={"scope": default_scope})
    return block


# Soglie (documentate qui per review esplicita
_BACKUP_WARN_HOURS = 26      # cron daily 02:00, tolleranza 2h jitter/retry
_BACKUP_ERROR_HOURS = 48

_VERIFY_WARN_DAYS = 8        # cron weekly Sun 03:00, tolleranza 1 giorno
_VERIFY_ERROR_DAYS = 14


# Backup (ultimo dump su S3


def get_backup_status() -> AdminStatusBlock:
    """Ultimo dump .dump caricato su S3 (il piu' recente per LastModified).

    Soglie:
      - ok: <26h
      - warn: 26h-48h
      - error: >48h
    """
    try:
        backups = list_backups()
    except Exception as e:
        logger.warning(f"list_backups fallito: {e}")
        return AdminStatusBlock(
            status="unknown",
            message="S3 non accessibile",
            error=str(e)[:200],
        )

    if not backups:
        return AdminStatusBlock(
            status="error",
            message="Nessun backup trovato su S3",
        )

    # list_backups ritorna gia' ordinato dal piu' recente
    latest = backups[0]
    last_modified = latest.get("last_modified")
    size_bytes = latest.get("size_bytes", 0)

    age_hours: Optional[float] = None
    if last_modified:
        if isinstance(last_modified, str):
            try:
                last_dt = datetime.fromisoformat(last_modified.replace("Z", "+00:00"))
            except ValueError:
                last_dt = None
        elif isinstance(last_modified, datetime):
            last_dt = last_modified
        else:
            last_dt = None

        if last_dt:
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600.0

    status = "ok"
    message = ""
    if age_hours is None:
        status = "unknown"
        message = f"Ultimo: {latest.get('key', '?')} (timestamp non parsabile)"
    elif age_hours > _BACKUP_ERROR_HOURS:
        status = "error"
        message = f"Ultimo backup {age_hours:.0f}h fa (> {_BACKUP_ERROR_HOURS}h)"
    elif age_hours > _BACKUP_WARN_HOURS:
        status = "warn"
        message = f"Ultimo backup {age_hours:.0f}h fa (atteso <{_BACKUP_WARN_HOURS}h)"
    else:
        if age_hours < 1:
            message = "Ultimo backup <1h fa"
        else:
            message = f"Ultimo backup {age_hours:.0f}h fa"

    return AdminStatusBlock(
        status=status,
        message=message,
        last_key=str(latest.get("key")) if latest.get("key") is not None else None,
        last_modified=last_modified if isinstance(last_modified, str) else (
            last_modified.isoformat() if last_modified else None
        ),
        size_bytes=size_bytes,
        age_hours=round(age_hours, 1) if age_hours is not None else None,
        total_backups=len(backups),
    )


# Verify restore (ultima esecuzione plenora-backup-test via DB
# Fonte di verita': plenora.verify_runs. Scritta da scripts/test_restore.py
# a fine job. Container-safe: nessuna dipendenza da systemctl/journalctl


def get_verify_restore_status() -> AdminStatusBlock:
    """Stato ultima esecuzione verify restore da plenora.verify_runs.

    Soglie weekly: ok < 8gg, warn 8-14gg, error > 14gg.
    """
    try:
        row = fetch_latest_verify_run()
    except Exception as e:
        logger.warning(f"query verify_runs fallita: {e}")
        return AdminStatusBlock(
            status="unknown",
            message="DB non accessibile",
            last_result="unknown",
            last_run_at=None,
            age_days=None,
            exit_code=None,
            error=str(e)[:200],
        )

    if not row:
        return AdminStatusBlock(
            status="unknown",
            message="Nessuna verifica registrata",
            last_result="unknown",
            last_run_at=None,
            age_days=None,
            exit_code=None,
        )

    result_value = row.get("result") or "unknown"
    result = result_value if isinstance(result_value, str) else str(result_value)
    exit_code_value = row.get("exit_code")
    exit_code = exit_code_value if isinstance(exit_code_value, int) else None
    finished_at = row.get("finished_at")
    error_message = row.get("error_message")

    age_days: Optional[float] = None
    last_run_iso: Optional[str] = None
    if finished_at is not None and hasattr(finished_at, "tzinfo"):
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        age_days = round((datetime.now(timezone.utc) - finished_at).total_seconds() / 86400.0, 1)
        last_run_iso = finished_at.isoformat()

    if result == "running":
        status = "ok"
        message = "Verifica in corso"
    elif result == "failed":
        status = "error"
        message = f"Ultima verifica FALLITA (exit {exit_code})"
        if error_message:
            message += f": {error_message[:80]}"
    elif result == "success":
        if age_days is None:
            status = "ok"
            message = "Ultima verifica OK"
        elif age_days > _VERIFY_ERROR_DAYS:
            status = "error"
            message = f"Ultima verifica OK ma {age_days:.0f}gg fa (>{_VERIFY_ERROR_DAYS}gg)"
        elif age_days > _VERIFY_WARN_DAYS:
            status = "warn"
            message = f"Ultima verifica {age_days:.0f}gg fa (atteso <{_VERIFY_WARN_DAYS}gg)"
        elif age_days < 1:
            status = "ok"
            message = "Verifica OK <1gg fa"
        else:
            status = "ok"
            message = f"Verifica OK {age_days:.0f}gg fa"
    else:
        status = "unknown"
        message = f"Stato verifica: {result}"

    return AdminStatusBlock(
        status=status,
        message=message,
        last_result=result,
        last_run_at=last_run_iso,
        age_days=age_days,
        exit_code=exit_code,
    )


# Schedules (DB query: attivi, failing recenti


def get_schedules_status() -> AdminStatusBlock:
    """Count schedule attivi + count failing — cross-group (superadmin)."""
    try:
        rows = fetch_schedule_rows()
    except Exception as e:
        logger.warning(f"query schedules fallita: {e}")
        return AdminStatusBlock(
            status="unknown",
            message="Schedule table non accessibile",
            error=str(e)[:200],
        )

    total_active = 0
    failing: list[ScheduleFailure] = []

    for row in rows:
        if not row.get("is_enabled"):
            continue
        total_active += 1
        last_status = row.get("last_status")
        if last_status in ("failed", "failing", "error"):
            failing.append(ScheduleFailure(
                id=str(row.get("id") or "?"),
                name=str(row.get("name") or "?"),
            ))

    if total_active == 0:
        return AdminStatusBlock(
            status="ok",
            message="Nessuno schedule attivo",
            total_active=0,
            failing_count=0,
            failing=[],
        )

    failing_count = len(failing)
    if failing_count == 0:
        status = "ok"
        message = f"{total_active} schedule attivi, tutti OK"
    elif failing_count >= total_active / 2:
        status = "error"
        message = f"{failing_count}/{total_active} schedule falliscono"
    else:
        status = "warn"
        message = f"{total_active} attivi, {failing_count} failing"

    return AdminStatusBlock(
        status=status,
        message=message,
        total_active=total_active,
        failing_count=failing_count,
        failing=failing[:5],  # max 5 nel payload
    )


# Celery workers (inspect.active via broker


def get_workers_status() -> AdminStatusBlock:
    """Worker Celery: nodi + slot concorrenza totali + task attivi + queue.

    Nota sulla semantica:
      - `nodes`: numero di processi master Celery (tipico: 1 per istanza EC2).
        Se hai N istanze/container, `nodes` diventa N.
      - `concurrency_slots`: somma dei pool size dei nodi (es. un nodo con
        `--concurrency=4` offre 4 slot). E' la capacita' pratica di task
        in parallelo.
      - `active_tasks`: task effettivamente in esecuzione adesso
        (<=concurrency_slots).
    """
    try:
        active, stats, queues_info = inspect_celery_workers(timeout=2.0)
    except CeleryAppUnavailable as e:
        logger.warning(f"celery_app import fallito: {e}")
        return AdminStatusBlock(
            status="unknown",
            message="Celery app non importabile",
            error=str(e)[:200],
        )
    except Exception as e:
        logger.warning(f"celery inspect fallito: {e}")
        return AdminStatusBlock(
            status="unknown",
            message="Broker Celery non raggiungibile",
            error=str(e)[:200],
        )

    nodes = 0
    concurrency_slots = 0
    active_tasks = 0
    details: list[WorkerDetail] = []

    try:
        nodes = len(active)
        active_tasks = sum(len(v or []) for v in active.values())

        # Per-node detail: dove gira + capacita' + carico + contatore lifetime
        # Union delle chiavi da active e stats (robusto a ritardi di
        # risposta di un endpoint dei due
        all_nodes = set(active.keys()) | set(stats.keys())
        for worker_name in sorted(all_nodes):
            s = stats.get(worker_name, {}) or {}
            pool = s.get("pool", {}) or {}
            max_conc = pool.get("max-concurrency") or 0
            processes = pool.get("processes") or []
            # `total` e' un dict {task_name: count}. Somma per ottenere il
            # totale lifetime di task processati dal nodo
            total_payload = s.get("total") or {}
            if isinstance(total_payload, dict):
                total_processed = sum(
                    v for v in total_payload.values() if isinstance(v, int)
                )
            elif isinstance(total_payload, int):
                total_processed = total_payload
            else:
                total_processed = None

            # Hostname dal naming Celery: "celery@hostname" -> "hostname"
            # Per pod Docker/K8s sara' es. "celery@plenora-worker-abc123"
            hostname = (
                worker_name.split("@", 1)[1]
                if "@" in worker_name else worker_name
            )

            if isinstance(max_conc, int) and max_conc > 0:
                concurrency_slots += max_conc

            # Code consumate dal nodo (utile se un giorno avremo queue multiple
            # es. "carica" dedicata o "priority"
            queue_names = [
                q.get("name") for q in (queues_info.get(worker_name) or [])
                if q.get("name")
            ]

            details.append(WorkerDetail(
                name=worker_name,
                hostname=hostname,
                pid=s.get("pid") if isinstance(s.get("pid"), int) else None,
                pool_size=max_conc if isinstance(max_conc, int) else None,
                child_processes=len(processes),
                active_tasks=len(active.get(worker_name, []) or []),
                total_tasks_processed=total_processed,
                queues=queue_names,
            ))
    except Exception as e:
        logger.warning(f"celery inspect fallito: {e}")
        return AdminStatusBlock(
            status="unknown",
            message="Broker Celery non raggiungibile",
            error=str(e)[:200],
        )

    # Queue depth via Redis diretto (best-effort
    # Usiamo settings.CELERY_BROKER_URL come unica variabile canonica
    # Redis dell'app (stesso broker che Celery stesso consuma
    queue_depth: Optional[int] = None
    try:
        queue_depth = get_celery_queue_depth("celery")
    except Exception as e:
        logger.debug(f"Redis queue depth non disponibile: {e}")

    status = "ok" if nodes > 0 else "error"
    if nodes == 0:
        message = "Nessun worker online"
    else:
        node_label = "1 nodo" if nodes == 1 else f"{nodes} nodi"
        slot_label = (
            f"{concurrency_slots} slot"
            if concurrency_slots > 0 else "pool size sconosciuto"
        )
        if active_tasks == 0:
            message = f"{node_label} · {slot_label} · idle"
        else:
            message = f"{node_label} · {slot_label} · {active_tasks} task attivi"
    if queue_depth is not None and queue_depth > 0:
        message += f" · {queue_depth} in coda"

    return AdminStatusBlock(
        status=status,
        message=message,
        nodes=nodes,
        concurrency_slots=concurrency_slots,
        active_tasks=active_tasks,
        queue_depth=queue_depth,
        details=details,
        # Retrocompat per chiamanti legacy (la vecchia chiave era workers_online
        workers_online=nodes,
    )


# Host resources (psutil

# Soglie host (documentate per review
_DISK_WARN_PCT = 80
_DISK_ERROR_PCT = 92
_MEM_WARN_PCT = 85
_MEM_ERROR_PCT = 95


def _disk_usage_probe_path(data_dir: Path) -> Path:
    """Return an existing path suitable for psutil.disk_usage().

    On Windows, psutil can fail on relative or not-yet-created paths. The
    metric we need is the filesystem containing DATA_DIR, so measuring the
    nearest existing parent is equivalent for capacity/usage purposes.
    """
    try:
        resolved = data_dir.expanduser().resolve(strict=False)
    except Exception:
        resolved = data_dir

    for candidate in (resolved, *resolved.parents):
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue

    try:
        return Path.cwd()
    except OSError:
        return Path(".")


def get_host_resources() -> AdminStatusBlock:
    """Metriche host via psutil: disco (sul mount DATA_DIR), RAM, CPU.

    In container/K8s misura le risorse del pod backend (cgroup limits se
    impostati, altrimenti quelle del nodo). In single-host misura la VM.

    Graceful: se psutil manca o `disk_usage` fallisce ritorna unknown.
    """
    try:
        import psutil
    except ImportError:
        return AdminStatusBlock(
            status="unknown",
            message="psutil non installato",
            scope="local_replica",
            disk=None,
            memory=None,
            cpu=None,
        )

    disk_info: Optional[DiskStatus] = None
    memory_info: Optional[MemoryStatus] = None
    cpu_info: Optional[CpuStatus] = None
    worst_status = "ok"

    # Disco sul mount di DATA_DIR
    data_dir = None
    usage_path = None
    psutil_error: Optional[BaseException] = None
    try:
        data_dir_path = Path(app_config.settings.DATA_DIR)
        data_dir = str(data_dir_path)
        usage_path = _disk_usage_probe_path(data_dir_path)
        # R-2 (2026-04-27): psutil.disk_usage emette
        # `SystemError("argument 1 (impossible<bad format char>)")` su Windows
        # locale (telemetria locale post-diagnostic patch). Causa probabile:
        # edge case in psutil._psplatform (Windows) sulla `struct` C.
        # Fallback: `shutil.disk_usage` (stdlib) usa GetDiskFreeSpaceExW
        # diretto e non passa per psutil. Se anche shutil fallisce -> graceful
        # con log enriched
        try:
            du = psutil.disk_usage(str(usage_path))
            total = du.total
            used = du.used
            free = du.free
            pct = round(du.percent, 1)
        except Exception as _pse:
            psutil_error = _pse
            su = _shutil.disk_usage(str(usage_path))
            total = su.total
            used = su.used
            free = su.free
            # shutil.disk_usage non espone `percent`: lo calcoliamo
            pct = round((used / total * 100.0) if total > 0 else 0.0, 1)
            logger.info(
                "host_resources disk: psutil.disk_usage fallito "
                f"({type(_pse).__module__}.{type(_pse).__name__}: {_pse!r}), "
                f"fallback shutil.disk_usage OK su usage_path={usage_path!r}"
            )
        disk_info = DiskStatus(
            path=data_dir,
            usage_path=str(usage_path),
            total_gb=round(total / (1024 ** 3), 2),
            used_gb=round(used / (1024 ** 3), 2),
            free_gb=round(free / (1024 ** 3), 2),
            percent=pct,
        )
        if pct >= _DISK_ERROR_PCT:
            worst_status = "error"
        elif pct >= _DISK_WARN_PCT and worst_status == "ok":
            worst_status = "warn"
    except Exception as e:
        # Entrambi i path (psutil + shutil) hanno fallito, o errore early
        # Path/settings). Diagnostica enriched per ridiagnosi rapida
        try:
            cwd_str = str(Path.cwd())
        except Exception:
            cwd_str = "<unavailable>"
        # Qualified type: alcune eccezioni famose hanno __name__ generico
        # es. struct.error.__name__ == 'error'), che da solo non aiuta
        # Includere il modulo rende il log azionabile a colpo d'occhio
        exc_type = type(e)
        exc_qualname = f"{exc_type.__module__}.{exc_type.__name__}"
        # Se psutil aveva fallito prima di shutil, includi entrambi i tipi
        # eccezione per debug
        psutil_part = ""
        if psutil_error is not None and psutil_error is not e:
            pet = type(psutil_error)
            psutil_part = (
                f" psutil_error={pet.__module__}.{pet.__name__}({psutil_error!r})"
            )
        logger.warning(
            "host_resources disk error: "
            f"{exc_qualname}({e!r}) | "
            f"data_dir={data_dir!r} usage_path={usage_path!r} "
            f"usage_path_type={type(usage_path).__name__} cwd={cwd_str!r}"
            f"{psutil_part}"
        )

    # Memoria
    try:
        import psutil
        vm = psutil.virtual_memory()
        pct = round(vm.percent, 1)
        memory_info = MemoryStatus(
            total_gb=round(vm.total / (1024 ** 3), 2),
            available_gb=round(vm.available / (1024 ** 3), 2),
            used_gb=round(vm.used / (1024 ** 3), 2),
            percent=pct,
        )
        if pct >= _MEM_ERROR_PCT:
            worst_status = "error"
        elif pct >= _MEM_WARN_PCT and worst_status != "error":
            worst_status = "warn"
    except Exception as e:
        logger.warning(f"host_resources memory error: {e}")

    # CPU: load avg (Linux) + cpu_percent
    try:
        import psutil
        try:
            load_1, load_5, load_15 = psutil.getloadavg()
        except (AttributeError, OSError):
            load_1 = load_5 = load_15 = None
        cpu_count = psutil.cpu_count(logical=True) or 1
        cpu_info = CpuStatus(
            count=cpu_count,
            load_1m=round(load_1, 2) if load_1 is not None else None,
            load_5m=round(load_5, 2) if load_5 is not None else None,
            load_15m=round(load_15, 2) if load_15 is not None else None,
            percent=round(psutil.cpu_percent(interval=None), 1),
        )
    except Exception as e:
        logger.warning(f"host_resources cpu error: {e}")

    parts = []
    if disk_info:
        parts.append(f"disco {disk_info.percent:.0f}%")
    if memory_info:
        parts.append(f"RAM {memory_info.percent:.0f}%")
    if cpu_info and cpu_info.percent is not None:
        parts.append(f"CPU {cpu_info.percent:.0f}%")
    message = " · ".join(parts) if parts else "Metriche non disponibili"

    return AdminStatusBlock(
        status=worst_status if (disk_info or memory_info or cpu_info) else "unknown",
        message=message,
        scope="local_replica",
        disk=disk_info,
        memory=memory_info,
        cpu=cpu_info,
    )


# DB pool stats (Postgres


def get_db_pool_status() -> AdminStatusBlock:
    """Stato del connection pool Postgres: attive/idle, lock, query lunghe.

    Usa `pg_stat_activity` per count + max connections. Soglia warn a
    80% del max, error a 95%.
    """
    try:
        row = fetch_db_pool_row()
    except Exception as e:
        logger.warning(f"db_pool status query failed: {e}")
        return AdminStatusBlock(
            status="unknown",
            message="DB non accessibile",
            error=str(e)[:200],
        )

    if not row:
        return AdminStatusBlock(
            status="unknown",
            message="pg_stat_activity vuoto",
        )

    max_conn = row.get("max_conn") or 100
    total = int(row.get("total") or 0)
    active = int(row.get("active") or 0)
    idle = int(row.get("idle") or 0)
    idle_in_tx = int(row.get("idle_in_tx") or 0)
    waiting = int(row.get("waiting") or 0)
    pct = round(100.0 * total / max_conn, 1) if max_conn else 0.0

    if pct >= 95:
        status = "error"
    elif pct >= 80:
        status = "warn"
    elif idle_in_tx > 5:  # molte sessioni appese su transaction aperta
        status = "warn"
    else:
        status = "ok"

    return AdminStatusBlock(
        status=status,
        message=f"{total}/{max_conn} conn ({pct:.0f}%) · {active} attive · {waiting} waiting",
        max_connections=max_conn,
        total=total,
        active=active,
        idle=idle,
        idle_in_transaction=idle_in_tx,
        waiting=waiting,
        percent=pct,
    )


# Redis info


def get_redis_status() -> AdminStatusBlock:
    """Stato Redis broker/cache: memory usage, clients connessi, uptime."""
    try:
        info = get_redis_info()
    except RedisClientUnavailable as e:
        return AdminStatusBlock(
            status="unknown",
            message="redis client non importabile",
            error=str(e)[:200],
        )
    except Exception as e:
        logger.warning(f"redis info failed: {e}")
        return AdminStatusBlock(
            status="error",
            message=f"Redis non raggiungibile: {type(e).__name__}",
            error=str(e)[:200],
        )

    used_bytes = int(info.get("used_memory", 0))
    used_human = str(info.get("used_memory_human", "?"))
    max_bytes = int(info.get("maxmemory", 0))
    clients = int(info.get("connected_clients", 0))
    uptime_sec = int(info.get("uptime_in_seconds", 0))

    # Se maxmemory=0 (unlimited), non possiamo calcolare la %
    pct: Optional[float] = None
    if max_bytes > 0:
        pct = round(100.0 * used_bytes / max_bytes, 1)

    status = "ok"
    if pct is not None:
        if pct >= 90:
            status = "error"
        elif pct >= 75:
            status = "warn"

    msg_parts = [f"mem {used_human}"]
    if pct is not None:
        msg_parts.append(f"({pct:.0f}% di {max_bytes // (1024*1024)}MB)")
    msg_parts.append(f"{clients} client")
    uptime_days = uptime_sec // 86400
    if uptime_days > 0:
        msg_parts.append(f"uptime {uptime_days}d")

    return AdminStatusBlock(
        status=status,
        message=" · ".join(msg_parts),
        used_memory_bytes=used_bytes,
        used_memory_human=used_human,
        max_memory_bytes=max_bytes,
        connected_clients=clients,
        uptime_seconds=uptime_sec,
        percent=pct,
    )


# JWT blacklist (Redis) — health dedicato


def get_blacklist_status() -> AdminStatusBlock:
    """Status della blacklist JWT (revoca token via /auth/logout).

    Wrapper su `app.core.security.token_blacklist.get_blacklist_status` che
    gestisce eventuali errori di import (es. test env senza il modulo).
    Soglia stale configurabile via env `JWT_BLACKLIST_STALE_THRESHOLD_SEC`
    (default 1800 = 30 min).

    Fa parte del contratto di graceful degradation della dashboard:
    mai solleva, sempre ritorna dict con `status` e `message`.
    """
    # BE-CONFIG-001C: default tipato in `Settings.DEFAULTS`
    # `JWT_BLACKLIST_STALE_THRESHOLD_SEC=1800`). Guard `ValueError` resta
    # safety per env malformata propagata dal type coercion
    try:
        threshold = int(
            app_config.settings.get("JWT_BLACKLIST_STALE_THRESHOLD_SEC")
        )
    except (TypeError, ValueError):
        threshold = 1800

    try:
        return AdminStatusBlock.model_validate(
            _probe(stale_threshold_seconds=threshold)
        )
    except Exception as e:
        # Safety net: get_blacklist_status e' gia' graceful, ma blindo
        logger.warning(f"get_blacklist_status wrapper fallito: {e}")
        return AdminStatusBlock(
            status="unknown",
            message="Probe blacklist fallita",
            error=str(e)[:200],
        )


# Ticketing — count attivi cross-group per dashboard superadmin


def get_tickets_status() -> AdminStatusBlock:
    """Aggregato ticket per card dashboard superadmin.

    Ritorna counts dei ticket in stato attivo (open, in_progress,
    waiting_user, reopened) cross-group + breakdown per severity.

    Soglie status:
      - `ok`: nessun critical + high <= 5
      - `warn`: high > 5 oppure 1-2 critical
      - `error`: >= 3 critical

    Graceful degradation: mai raise, ritorna sempre dict con status.
    """
    try:
        metrics = get_active_ticket_metrics()
        total_active = int(metrics.total_active or 0)
        by_severity = metrics.by_severity or {}
        # Coerce esplicito: by_severity proviene da una query SQL
        # GROUP BY severity COUNT(*) che psycopg2 puo' tipizzare come
        # Decimal/str a seconda del backend. Senza int() i confronti
        # `critical > 0` downstream possono dare risultati inattesi.
        critical = int(by_severity.get("critical", 0) or 0)
        high = int(by_severity.get("high", 0) or 0)
    except Exception as e:
        logger.warning(f"get_tickets_status DB error: {e}")
        return AdminStatusBlock(
            status="unknown",
            message="Query ticket fallita",
            error=str(e)[:200],
        )

    if critical >= 3:
        status = "error"
        message = f"{critical} ticket critici aperti"
    elif critical > 0 or high > 5:
        status = "warn"
        parts = []
        if critical:
            parts.append(f"{critical} critical")
        if high:
            parts.append(f"{high} high")
        message = "Ticket attivi: " + " · ".join(parts)
    else:
        status = "ok"
        if total_active == 0:
            message = "Nessun ticket attivo"
        else:
            message = f"{total_active} ticket attivi (nessuno critico)"

    return AdminStatusBlock(
        status=status,
        message=message,
        total_active=total_active,
        by_severity=by_severity,
    )


# DB schema size + row counts tabelle critiche

# Tabelle da tracciare per growth insights (oversize detection
_CRITICAL_TABLES = [
    "file_sources",
    "results",
    "schedule_logs",
    "backup_runs",
    "verify_runs",
    "intermediate_runs",
    "logs",
    "tickets",
    "ticket_comments",
    "ticket_attachments",
]


def get_db_schema_size() -> AdminStatusBlock:
    """Dimensione schema `plenora` + row count tabelle critiche.

    Utile per early-detection di growth anomali (es. file_sources.data
    pre-FS-B1-large fix gonfiato con blob >50MB).
    """
    try:
        size_row = fetch_schema_size_row()
        table_rows = fetch_critical_table_rows(_CRITICAL_TABLES)
    except Exception as e:
        logger.warning(f"db_schema_size query failed: {e}")
        return AdminStatusBlock(
            status="unknown",
            message="DB non accessibile",
            error=str(e)[:200],
        )

    size_bytes = int((size_row or {}).get("size_bytes") or 0)
    size_pretty = str((size_row or {}).get("size_pretty") or "0 bytes")
    tables = [
        DbSchemaTableStatus(
            name=str(r.get("table")) if r.get("table") is not None else None,
            rows=int(r.get("rows") or 0),
            size_bytes=int(r.get("size_bytes") or 0),
            size_pretty=str(r.get("size_pretty") or "0 bytes"),
        )
        for r in (table_rows or [])
    ]

    # Soglie: dumping cresciute molto potrebbe indicare bytea bloat
    # Error a 5 GB, warn a 1 GB. Tweakable via env
    # BE-CONFIG-001D: default tipati `float` in `Settings.DEFAULTS`
    # `DB_SCHEMA_WARN_GB=1.0`, `DB_SCHEMA_ERROR_GB=5.0`). Override env
    # via `Settings._converti_tipo_env` (coercion `float`
    warn_gb = float(app_config.settings.get("DB_SCHEMA_WARN_GB"))
    error_gb = float(app_config.settings.get("DB_SCHEMA_ERROR_GB"))
    size_gb = size_bytes / (1024 ** 3)
    if size_gb >= error_gb:
        status = "error"
    elif size_gb >= warn_gb:
        status = "warn"
    else:
        status = "ok"

    return AdminStatusBlock(
        status=status,
        message=f"schema plenora: {size_pretty}",
        size_bytes=size_bytes,
        size_pretty=size_pretty,
        size_gb=round(size_gb, 3),
        tables=tables,
    )


# Aggregator


def get_system_status() -> SystemStatusPayload:
    """Ritorna il JSON aggregato per la dashboard.

    Ogni metrica e' un blocco separato: errori in una NON bloccano le altre.
    """
    generated_at = datetime.now(timezone.utc)
    return SystemStatusPayload(
        generated_at=generated_at,
        backup=_as_status_block(get_backup_status()),
        verify_restore=_as_status_block(get_verify_restore_status()),
        schedules=_as_status_block(get_schedules_status()),
        workers=_as_status_block(get_workers_status()),
        host_resources=_as_status_block(
            get_host_resources(),
            default_scope="local_replica",
        ),
        db_pool=_as_status_block(get_db_pool_status()),
        redis=_as_status_block(get_redis_status()),
        db_schema=_as_status_block(get_db_schema_size()),
        jwt_blacklist=_as_status_block(get_blacklist_status()),
        tickets=_as_status_block(get_tickets_status()),
    )
