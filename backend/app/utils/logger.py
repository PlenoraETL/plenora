"""
Sistema di logging strutturato per PLENORA-ETL — storage su PostgreSQL.

Architettura (v3)
=================
Logging a doppia destinazione:
    1. Console (INFO+) — feedback sviluppo, formato compatto
    2. PostgreSQL — tabella `logs` per query veloci dal frontend

Schema tabella logs:
    id         SERIAL PRIMARY KEY
    ts         TIMESTAMPTZ NOT NULL
    level      TEXT    NOT NULL  (INFO/WARNING/ERROR)
    module     TEXT    NOT NULL  (estrai/manipola/...)
    op         TEXT              (execute/preview/...)
    msg        TEXT    NOT NULL
    project_id TEXT
    details    JSONB             (JSON)
    error      TEXT              (traceback se presente)

Uso:
    from app.utils.logger import get_logger, log_event

    logger = get_logger(__name__)
    logger.info("Estratte 1500 righe", op="execute", project_id="abc123",
                details={"rows": 1500, "duration_ms": 340})
"""

import json
import logging
from contextvars import ContextVar
from typing import List, Optional

# C8: contesto via ContextVar (non threading.local). ContextVar propaga
# attraverso asyncio: `asyncio.create_task()`, `asyncio.gather()`, e
# `asyncio.to_thread()` copiano il Context corrente nel task figlio,
# quindi run_id/request_id/group_id restano visibili nel codice async.
# threading.Thread NON copia Context — per scenari multi-thread
# espliciti serve contextvars.copy_context() esplicito, ma nei nostri
# path (FastAPI handler + Celery worker + thread pool arricchimenti)
# funziona out-of-the-box.
_group_id_var: ContextVar[str] = ContextVar("log_group_id", default="")
_run_id_var: ContextVar[str] = ContextVar("log_run_id", default="")
_project_id_var: ContextVar[str] = ContextVar("log_project_id", default="")
_steps_var: ContextVar[Optional[List[dict]]] = ContextVar("log_steps", default=None)
_request_id_var: ContextVar[str] = ContextVar("log_request_id", default="")
_user_id_var: ContextVar[str] = ContextVar("log_user_id", default="")


def set_log_group(group_id: str):
    """Imposta il group_id nel contesto corrente (chiamato dal middleware)."""
    _group_id_var.set(group_id or "")


def get_log_group() -> str:
    return _group_id_var.get() or ""


def set_run_context(run_id: str, project_id: str = "") -> None:
    """Imposta run_id e project_id nel contesto. Chiamato da pipeline_task."""
    _run_id_var.set(run_id or "")
    _project_id_var.set(project_id or "")
    _steps_var.set([])


def get_run_id() -> str:
    return _run_id_var.get() or ""


def append_step(step: dict) -> None:
    """Appende uno step alla lista della run corrente. Chiamato dagli executor."""
    steps = _steps_var.get()
    if steps is None:
        steps = []
        _steps_var.set(steps)
    steps.append(step)


def get_steps() -> list:
    """Restituisce gli step raccolti finora nella run corrente."""
    steps = _steps_var.get()
    return list(steps) if steps else []


def set_request_id(request_id: str) -> None:
    """C8: correlation ID propagato da HTTP middleware e Celery signals."""
    _request_id_var.set(request_id or "")


def get_request_id() -> str:
    return _request_id_var.get() or ""


def set_user_id(user_id: str) -> None:
    """C8: user_id propagato dal middleware JWT."""
    _user_id_var.set(user_id or "")


def get_user_id() -> str:
    return _user_id_var.get() or ""

import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

from app.config import settings


# =============================================================================
# SANIFICAZIONE LOG (difesa in profondità contro leak credenziali)
# =============================================================================
# Anche se i call site dovrebbero sanificare a monte (vedi
# app/shared/api_utils.sanitize_for_log / sanitize_url_for_log), il logger
# applica un secondo passaggio sui campi di output per intercettare casi come
# `requests.HTTPError` la cui __str__ contiene "for url: https://.../?token=XYZ".

# Pattern di chiavi sensibili (case-insensitive)
_SENSITIVE_KEY_NAMES = (
    "token", "api_key", "apikey", "password", "passwd", "secret",
    "authorization", "auth_key", "access_key", "client_secret",
    "private_key", "x-api-key",
)
# Regex: catch "key=value" dove value non contiene & o spazi.
# Costruita per matchare anche varianti come "ApiKey", "API_KEY", "x-api-key".
_QS_REDACT_RE = re.compile(
    r"(?i)([?&]|^)((?:" + "|".join(re.escape(k) for k in _SENSITIVE_KEY_NAMES) + r")=)[^&\s'\"]+",
)
# Redazione anche di basic auth in URL: scheme://user:pass@host
_BASIC_AUTH_RE = re.compile(r"(?i)(https?://)([^:/@\s]+):([^@\s]+)@")

_REDACTED = "***REDACTED***"


def _sanitize_log_text(text: str) -> str:
    """Sanifica un testo destinato ai log oscurando URL credentials e query string sensibili.

    Best-effort, regex-based: pensata per testi liberi (msg, traceback) dove
    non si può applicare un parser URL strutturato. Idempotente.
    """
    if not text or not isinstance(text, str):
        return text
    # Query string sensibili: ?token=xxx → ?token=***REDACTED***
    text = _QS_REDACT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    # Basic auth in URL: https://user:pass@host → https://***REDACTED***@host
    text = _BASIC_AUTH_RE.sub(lambda m: f"{m.group(1)}{_REDACTED}@", text)
    return text


class SanitizingFilter(logging.Filter):
    """Filter che sanifica msg + traceback del LogRecord prima degli handler.

    Si applica a TUTTI gli handler (console + SQLite + future destinazioni),
    quindi è il punto unico di difesa contro leak via log. Idempotente con
    la sanificazione esplicita in SQLiteLogHandler.emit().
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Sanifica il messaggio interpolato. Mettiamo il risultato in record.msg
        # e azzeriamo args perché getMessage() farebbe di nuovo msg % args.
        try:
            interpolated = record.getMessage()
            record.msg = _sanitize_log_text(interpolated)
            record.args = ()
        except Exception:
            pass
        # Pre-formatta e sanifica la traceback in exc_text. I Formatter standard
        # usano exc_text se presente invece di rifare format_exception.
        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = _sanitize_log_text(
                    "".join(traceback.format_exception(*record.exc_info))
                )
            except Exception:
                pass
        return True


# Singleton filter — uno per tutta l'app
_sanitizing_filter = SanitizingFilter()

# =============================================================================
# COSTANTI
# =============================================================================

CONSOLE_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
DATE_FORMAT    = "%H:%M:%S"

# =============================================================================
# COLORED CONSOLE FORMATTER
# =============================================================================
# Colori ANSI — applicati solo su stdout, non su log.db (zero impatto sul DB)

_ANSI_RESET  = "[0m"
_ANSI_BOLD   = "[1m"

_LEVEL_COLORS = {
    "DEBUG":   "[37m",    # bianco tenue
    "INFO":    "[36m",    # ciano
    "WARNING": "[33m",    # giallo
    "ERROR":   "[31m",    # rosso
}

# Colori specifici per i tag dentro ERROR
_TAG_COLORS = {
    "[CRASH]":   "[1;31m",   # rosso bold
    "[GESTITO]": "[33m",     # arancione/giallo (stesso di WARNING — errore atteso)
}


class ColoredFormatter(logging.Formatter):
    """
    Formatter con colori ANSI per la console.
    - INFO    -> ciano
    - WARNING -> giallo
    - ERROR [GESTITO] -> giallo/arancione (errore gestito, sistema stabile)
    - ERROR [CRASH]   -> rosso bold (bug imprevisto, da investigare)
    - log.db NON è impattato: il SQLiteLogHandler riceve il LogRecord grezzo
      prima che questo formatter entri in gioco.
    """

    def format(self, record: logging.LogRecord) -> str:
        # C8: prefisso request_id abbreviato (8 char) se presente nel
        # contesto — rende ogni riga correlata a una richiesta HTTP
        # senza cambiare il formato CONSOLE_FORMAT del logger.
        req_id = get_request_id()
        if req_id:
            record.msg = f"[req={req_id[:8]}] {record.msg}"
        msg = super().format(record)
        level = record.levelname

        # Determina colore base dal livello
        color = _LEVEL_COLORS.get(level, "")

        # Per ERROR: distingue CRASH da GESTITO
        if level == "ERROR":
            raw_msg = record.getMessage()
            for tag, tag_color in _TAG_COLORS.items():
                if tag in raw_msg:
                    color = tag_color
                    break

        if color:
            return f"{color}{msg}{_ANSI_RESET}"
        return msg

LOG_DB_FILE     = settings.LOG_DB_FILE
LOG_DB_MAX_ROWS = settings.LOG_DB_MAX_ROWS

MODULE_MAP = {
    "app.core.estrai":        "estrai",
    "app.core.manipola":      "manipola",
    "app.core.arricchisci":   "arricchisci",
    "app.core.carica":        "carica",
    "app.core.scheduler":     "scheduler",
    "app.core.orchestrator":  "orchestrator",
    "app.core.connections":   "connections",
    "app.core.projects":      "projects",
    "app.core.files":         "files",
    "app.services":           "services",
    "app.db":                 "db",
    "app.utils":              "utils",
    "workers":                "workers",
    "urby.carica":            "carica",
    "app":                    "app",
}

_configured_loggers: set[str] = set()
_db_handler: Optional["SQLiteLogHandler"] = None
_db_lock = Lock()


# =============================================================================
# SQLITE HANDLER
# =============================================================================

def _resolve_level(name: str, default: int) -> int:
    """Converte il nome del livello (es. 'WARNING') nel costante int.
    Fallback al default se il nome non e' valido. Niente import di settings
    al top-level per evitare cicli: i settings passano il nome come stringa."""
    if not name:
        return default
    v = getattr(logging, str(name).upper(), None)
    return v if isinstance(v, int) else default


class PostgresLogHandler(logging.Handler):
    """Handler logging che scrive su PostgreSQL via pg_connection pool."""

    def __init__(self):
        # Livello configurabile via settings.LOG_LEVEL_DB (default WARNING).
        # Lazy import per evitare cicli al bootstrap.
        try:
            from app.config import settings as _s
            lvl = _resolve_level(_s.LOG_LEVEL_DB, logging.WARNING)
        except Exception:
            lvl = logging.WARNING
        super().__init__(lvl)
        # Le tabelle logs e log_runs sono già create dallo schema PG

    def emit(self, record: logging.LogRecord):
        try:
            from app.db.pg_connection import execute as pg_execute
            ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            level = record.levelname
            module = _resolve_module(record.name)
            op = getattr(record, "op", "") or ""
            msg = _sanitize_log_text(record.getMessage())
            project_id = getattr(record, "project_id", "") or ""
            group_id = getattr(record, "group_id", "") or get_log_group()
            run_id = getattr(record, "run_id", "") or get_run_id() or ""
            node_id = getattr(record, "node_id", "") or ""
            details_raw = getattr(record, "details", None)
            # C8: injetta request_id + user_id dal contesto nel payload
            # details (evita schema migration). Se details ha gia' questi
            # campi, non li sovrascriviamo.
            req_id = get_request_id()
            uid = get_user_id()
            if req_id or uid:
                if details_raw is None:
                    details_raw = {}
                elif not isinstance(details_raw, dict):
                    details_raw = {"value": details_raw}
                if req_id and "request_id" not in details_raw:
                    details_raw["request_id"] = req_id
                if uid and "user_id" not in details_raw:
                    details_raw["user_id"] = uid
            details = json.dumps(details_raw, ensure_ascii=False, default=str) if details_raw else None
            if details:
                details = _sanitize_log_text(details)
            error = ""
            if record.exc_info and record.exc_info[1]:
                error = _sanitize_log_text(
                    "".join(traceback.format_exception(*record.exc_info)).strip()
                )

            pg_execute(
                "INSERT INTO logs (ts, level, module, op, msg, project_id, group_id, run_id, node_id, details, error) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ts, level, module, op, msg, project_id, group_id, run_id, node_id, details, error)
            )
        except Exception:
            self.handleError(record)

    def open_run(
        self,
        run_id: str,
        project_id: str,
        group_id: str = "",
        module: str = "",
        triggered_by: str = "manual",
        user_id: Optional[str] = None,
    ) -> None:
        """Apre una nuova run — status 'running'.

        N9: `user_id` viene persistito per audit (chi ha lanciato la run).
        None per task cron/scheduled senza user umano dietro.
        """
        from app.db.pg_connection import execute as pg_execute
        ts_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        pg_execute(
            "INSERT INTO log_runs "
            "(run_id, project_id, group_id, module, triggered_by, ts_start, status, user_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,'running',%s) ON CONFLICT DO NOTHING",
            (run_id, project_id, group_id, module, triggered_by, ts_start, user_id),
        )

    def close_run(
        self,
        run_id: str,
        status: str,
        rows_in: int = 0,
        rows_out: int = 0,
        duration_ms: int = 0,
        steps: list = None,
        error_node_id: str = None,
        error_module: str = None,
        error_message: str = None,
        debug_payload: dict = None,
    ) -> None:
        """Chiude una run aggiornando tutti i campi di esito."""
        from app.db.pg_connection import execute as pg_execute
        ts_end = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        steps_json = json.dumps(steps or [], ensure_ascii=False, default=str)
        payload_json = json.dumps(debug_payload, ensure_ascii=False, default=str) if debug_payload else None
        pg_execute(
            """UPDATE log_runs SET
                ts_end        = %s,
                status        = %s,
                rows_in       = %s,
                rows_out      = %s,
                duration_ms   = %s,
                steps         = %s,
                error_node_id = %s,
                error_module  = %s,
                error_message = %s,
                debug_payload = %s
            WHERE run_id = %s""",
            (
                ts_end, status, rows_in, rows_out, duration_ms,
                steps_json, error_node_id, error_module, error_message,
                payload_json, run_id,
            ),
        )


def _resolve_module(name: str) -> str:
    for prefix, short in MODULE_MAP.items():
        if name.startswith(prefix):
            return short
    return name.split(".")[-1] if "." in name else name


def _get_db_handler() -> PostgresLogHandler:
    global _db_handler
    if _db_handler is None:
        _db_handler = PostgresLogHandler()
    return _db_handler


# =============================================================================
# STRUCTURED LOGGER
# =============================================================================

class StructuredLogger:
    """
    Logger strutturato con supporto op/project_id/details.

    Uso:
        logger = get_logger(__name__)
        logger.info("Estratte 1500 righe", op="execute", project_id="p_001",
                    details={"rows": 1500})
        logger.error("[CRASH] Connessione fallita", op="connect", exc_info=True)
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, msg: str, *args,
             op: str = "", project_id: str = "", run_id: str = "",
             node_id: str = "", details: dict = None,
             exc_info=None, **kwargs):
        extra = {"op": op}
        if project_id:
            extra["project_id"] = project_id
        if run_id:
            extra["run_id"] = run_id
        if node_id:
            extra["node_id"] = node_id
        if details:
            extra["details"] = details
        self._logger.log(level, msg, *args, exc_info=exc_info, extra=extra, **kwargs)

    def debug(self, msg: str, *args, **kwargs):
        # Nota: se il metodo manca, qualunque logger.debug() in giro solleva
        # AttributeError. Era successo con il fix del log volume di aprile 2026:
        # enrichment_service.py passato da logger.info a logger.debug →
        # AttributeError → catturato dal catch-all async → fallimento silente.
        self._log(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        self._log(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        self._log(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        self._log(logging.ERROR, msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs):
        kwargs.setdefault("exc_info", True)
        self._log(logging.ERROR, msg, *args, **kwargs)

    @property
    def level(self):
        return self._logger.level

    def setLevel(self, level):
        self._logger.setLevel(level)

    def isEnabledFor(self, level):
        return self._logger.isEnabledFor(level)

    def log(self, level: int, msg: str, *args, extra=None, **kwargs):
        self._logger.log(level, msg, *args, extra=extra, **kwargs)


def get_logger(name: str, level: int = None) -> StructuredLogger:
    """
    Restituisce un StructuredLogger con 2 destinazioni:
        1. Console stdout (livello LOG_LEVEL_CONSOLE, default INFO)
        2. PostgreSQL tabella logs (livello LOG_LEVEL_DB, default WARNING)
    Il livello sul logger stesso e' il minimo dei due handler, cosi' i record
    sotto LOG_LEVEL_CONSOLE ma sopra LOG_LEVEL_DB arrivano comunque al DB.
    """
    logger = logging.getLogger(name)

    if name not in _configured_loggers:
        # Lazy import per evitare cicli durante il bootstrap di config.
        try:
            from app.config import settings as _s
            console_lvl = _resolve_level(_s.LOG_LEVEL_CONSOLE, logging.INFO)
            db_lvl      = _resolve_level(_s.LOG_LEVEL_DB,      logging.WARNING)
        except Exception:
            console_lvl = logging.INFO
            db_lvl      = logging.WARNING

        # Il logger lascia passare tutto quello che almeno uno degli handler
        # potrebbe voler emettere.
        effective = level if level is not None else min(console_lvl, db_lvl)
        logger.setLevel(effective)
        logger.propagate = False

        # Filter di sanificazione (applicato PRIMA degli handler).
        # Sanifica msg + traceback per oscurare credentials/token nei log.
        logger.addFilter(_sanitizing_filter)

        # Console
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(console_lvl)
        console.setFormatter(ColoredFormatter(CONSOLE_FORMAT, DATE_FORMAT))
        logger.addHandler(console)

        # PostgreSQL (il livello e' gia' settato nel __init__ di PostgresLogHandler)
        logger.addHandler(_get_db_handler())

        _configured_loggers.add(name)

    return StructuredLogger(logger)


def log_event(
    level: str,
    module: str,
    op: str,
    msg: str,
    *,
    project_id: str = "",
    details: dict = None,
):
    """Emette un evento di log strutturato."""
    logger = get_logger(f"app.core.{module}")
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    extra = {"op": op}
    if project_id:
        extra["project_id"] = project_id
    if details:
        extra["details"] = details
    logger.log(numeric_level, msg, extra=extra)


# Logger globale
app_logger = get_logger("app")


# =============================================================================
# FUNZIONI MODULO — open/close run
# =============================================================================

def open_run(
    run_id: str,
    project_id: str,
    group_id: str = "",
    module: str = "",
    triggered_by: str = "manual",
    user_id: Optional[str] = None,
) -> None:
    """Apre una run su log_runs (Postgres). Chiamare all'avvio del task.

    N9: user_id viene persistito per audit (nullable, default None per
    cron/scheduled).
    """
    _get_db_handler().open_run(
        run_id=run_id,
        project_id=project_id,
        group_id=group_id,
        module=module,
        triggered_by=triggered_by,
        user_id=user_id,
    )


def close_run(
    run_id: str,
    status: str,
    rows_in: int = 0,
    rows_out: int = 0,
    duration_ms: int = 0,
    steps: list = None,
    error_node_id: str = None,
    error_module: str = None,
    error_message: str = None,
    debug_payload: dict = None,
) -> None:
    """Chiude una run su log.db. Chiamare alla fine del task pipeline."""
    _get_db_handler().close_run(
        run_id=run_id,
        status=status,
        rows_in=rows_in,
        rows_out=rows_out,
        duration_ms=duration_ms,
        steps=steps,
        error_node_id=error_node_id,
        error_module=error_module,
        error_message=error_message,
        debug_payload=debug_payload,
    )

