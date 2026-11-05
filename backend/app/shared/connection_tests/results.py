"""
Esito del «Test connessione» delle connessioni salvate (Fase 5, voce S3).

REGOLA: ogni endpoint che testa una connessione SALVATA registra l'esito con
UNA sola funzione (``record_connection_test``, applicata dal decoratore
``records_connection_test`` sulla route): riuscito o no, messaggio senza
segreti (``error_sanitizer.sanitize_http_detail``), data/ora UTC, utente
che l'ha eseguito, durata. Le liste delle connessioni portano l'ultimo esito
in ``last_test`` (decoratore ``attaches_connection_tests`` sulla route della
lista); un esito piu' vecchio di ``STALE_AFTER`` ha ``stale = true`` e
l'interfaccia lo mostra come «Da riverificare». Una modifica salvata o la
cancellazione della connessione azzera l'esito (decoratore
``resets_connection_test`` sulle route PUT/DELETE): la configurazione
testata non e' piu' quella salvata. Un test partito prima dell'azzeramento
non lo sovrascrive (``reset_at`` nella tabella).

Permessi: l'esito si registra solo quando la route del test risponde, cioe'
dopo i suoi controlli di visibilita'; si legge solo dentro le liste, che
mostrano le connessioni visibili all'utente.

Non si registra:
 - un test che la route rifiuta (404/403, chiave di cifratura assente:
   la route solleva prima di rispondere);
 - il test di una configurazione NON salvata (``POST /api-connections/test``)
   o di modifiche non ancora salvate (``config_overrides``): non descrive la
   connessione salvata (``when`` del decoratore).

Il test ``tests2/shared/test_connection_test_results_contract.py`` enumera le
route dell'app: ogni POST ``.../test`` e' decorato (o e' un'eccezione
dichiarata), ogni PUT/DELETE della stessa risorsa azzera l'esito e la lista
del tipo lo allega.
"""

from __future__ import annotations

import functools
import inspect
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence

import psycopg
from app.db.pg_connection import unit_of_work
from app.shared.contracts.connection_test_status import ConnectionTestStatus
from app.shared.error_sanitizer import sanitize_http_detail
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Dopo quanto un esito va riverificato (l'interfaccia mostra «Da riverificare»).
STALE_AFTER = timedelta(days=30)

# Lunghezza massima del messaggio salvato (dopo la sanitizzazione).
MESSAGE_MAX_LEN = 300

# I tipi di connessione che hanno un test. Chiave = ``resource_kind`` nella
# tabella e nei decoratori.
CONNECTION_TEST_KINDS: Mapping[str, str] = {
    "connection": "Connessione database",
    "arcgis_connection": "Connessione ArcGIS",
    "datasource": "Data Source",
    "api_connection": "Connettore API",
}

# Marcatori messi dai decoratori sulla funzione della route (letti dal test
# di enumerazione): records/resets = (tipo, parametro dell'id), attaches = tipo.
RECORDS_ATTR = "__connection_test_records__"
RESETS_ATTR = "__connection_test_resets__"
ATTACHES_ATTR = "__connection_test_attaches__"

_SUCCESS_FALLBACK = "Connessione riuscita"
_FAILURE_FALLBACK = "Test non riuscito"


@dataclass(frozen=True)
class ConnectionTestRecord:
    kind: str
    resource_id: str
    success: bool
    message: str
    tested_at: datetime
    tested_by: Optional[str]
    tested_by_name: Optional[str]
    duration_ms: int


class ConnectionTestStore(Protocol):
    def record(self, entry: ConnectionTestRecord) -> None: ...

    def reset(self, kind: str, resource_id: str, at: datetime) -> None: ...

    def load(self, kind: str, resource_ids: Sequence[str]) -> Sequence[Mapping[str, Any]]: ...


_RECORD_SQL = (
    "INSERT INTO connection_test_results "
    "(resource_kind, resource_id, success, message, tested_at, tested_by, "
    " tested_by_name, duration_ms) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (resource_kind, resource_id) DO UPDATE SET "
    "success = EXCLUDED.success, message = EXCLUDED.message, "
    "tested_at = EXCLUDED.tested_at, tested_by = EXCLUDED.tested_by, "
    "tested_by_name = EXCLUDED.tested_by_name, duration_ms = EXCLUDED.duration_ms "
    # Un test partito prima di un azzeramento (modifica) o di un test piu'
    # recente non sovrascrive.
    "WHERE (connection_test_results.reset_at IS NULL "
    "       OR connection_test_results.reset_at < EXCLUDED.tested_at) "
    "  AND (connection_test_results.tested_at IS NULL "
    "       OR connection_test_results.tested_at <= EXCLUDED.tested_at)"
)

_RESET_SQL = (
    "INSERT INTO connection_test_results (resource_kind, resource_id, reset_at) "
    "VALUES (%s, %s, %s) "
    "ON CONFLICT (resource_kind, resource_id) DO UPDATE SET "
    "success = NULL, message = NULL, tested_at = NULL, tested_by = NULL, "
    "tested_by_name = NULL, duration_ms = NULL, reset_at = EXCLUDED.reset_at"
)

_LOAD_SQL = (
    "SELECT resource_id, success, message, tested_at, tested_by_name, duration_ms "
    "FROM connection_test_results "
    "WHERE resource_kind = %s AND resource_id = ANY(%s) AND tested_at IS NOT NULL"
)


class PgConnectionTestStore:
    """Tabella ``connection_test_results`` (migrazione 2026-11-05)."""

    def record(self, entry: ConnectionTestRecord) -> None:
        with unit_of_work() as session:
            session.execute(
                _RECORD_SQL,
                (
                    entry.kind,
                    entry.resource_id,
                    entry.success,
                    entry.message,
                    entry.tested_at,
                    entry.tested_by,
                    entry.tested_by_name,
                    entry.duration_ms,
                ),
            )

    def reset(self, kind: str, resource_id: str, at: datetime) -> None:
        with unit_of_work() as session:
            session.execute(_RESET_SQL, (kind, resource_id, at))

    def load(self, kind: str, resource_ids: Sequence[str]) -> Sequence[Mapping[str, Any]]:
        with unit_of_work() as session:
            return session.fetchall(_LOAD_SQL, (kind, list(resource_ids)))


_store: ConnectionTestStore = PgConnectionTestStore()

# Errori del DB (pool esaurito, tabella assente, DATABASE_URL mancante):
# l'esito e' un'informazione accessoria, non deve far fallire il test ne' la
# lista. PoolTimeout di psycopg_pool e' un psycopg.OperationalError.
_STORE_ERRORS = (psycopg.Error, RuntimeError)


def _check_kind(kind: str) -> None:
    if kind not in CONNECTION_TEST_KINDS:
        raise ValueError(f"Tipo di connessione senza test: {kind!r}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _field(result: Any, name: str) -> Any:
    if isinstance(result, Mapping):
        return result.get(name)
    return getattr(result, name, None)


def outcome_of(result: Any) -> tuple[bool, str]:
    """Esito e messaggio sanitizzato dalla risposta di un test.

    Le route rispondono con ``{success, message}`` (database, ArcGIS, Data
    Source) o ``{success, error}`` (connettori API).
    """
    success = _field(result, "success") is True
    raw = _field(result, "message") or _field(result, "error")
    if not raw:
        status_code = _field(result, "status_code")
        raw = _SUCCESS_FALLBACK if success else _FAILURE_FALLBACK
        if isinstance(status_code, int):
            raw = f"{raw} (HTTP {status_code})"
    return success, sanitize_http_detail(str(raw), max_len=MESSAGE_MAX_LEN)


def _user_of(kwargs: Mapping[str, Any]) -> tuple[Optional[str], Optional[str]]:
    user = kwargs.get("current_user")
    user_id = getattr(user, "user_id", None)
    username = getattr(user, "username", None)
    return (
        user_id if isinstance(user_id, str) and user_id else None,
        username if isinstance(username, str) and username else None,
    )


def record_connection_test(
    kind: str,
    resource_id: str,
    result: Any,
    *,
    started_at: datetime,
    duration_ms: int,
    user_id: Optional[str],
    username: Optional[str],
) -> None:
    """LA funzione che registra l'esito di un test (fail-open)."""
    _check_kind(kind)
    success, message = outcome_of(result)
    entry = ConnectionTestRecord(
        kind=kind,
        resource_id=resource_id,
        success=success,
        message=message,
        tested_at=started_at,
        tested_by=user_id,
        tested_by_name=username,
        duration_ms=max(0, int(duration_ms)),
    )
    try:
        _store.record(entry)
    except _STORE_ERRORS as exc:
        logger.warning(
            "connection_test_record_failed kind=%s id=%s: %s",
            kind, resource_id, type(exc).__name__,
        )


def reset_connection_test(kind: str, resource_id: str) -> None:
    """Azzera l'esito (modifica o cancellazione della connessione), fail-open."""
    _check_kind(kind)
    try:
        _store.reset(kind, resource_id, _now())
    except _STORE_ERRORS as exc:
        logger.warning(
            "connection_test_reset_failed kind=%s id=%s: %s",
            kind, resource_id, type(exc).__name__,
        )


def _as_aware(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def last_connection_tests(
    kind: str,
    resource_ids: Iterable[str],
    *,
    now: Optional[datetime] = None,
) -> Mapping[str, ConnectionTestStatus]:
    """Ultimo esito per id (solo le connessioni testate dopo l'ultima modifica)."""
    _check_kind(kind)
    ids = [rid for rid in dict.fromkeys(resource_ids) if isinstance(rid, str) and rid]
    if not ids:
        return {}
    try:
        rows = _store.load(kind, ids)
    except _STORE_ERRORS as exc:
        logger.warning(
            "connection_test_load_failed kind=%s: %s", kind, type(exc).__name__,
        )
        return {}
    current = now or _now()
    out: dict[str, ConnectionTestStatus] = {}
    for row in rows:
        tested_at = _as_aware(row.get("tested_at"))
        rid = row.get("resource_id")
        if tested_at is None or not isinstance(rid, str):
            continue
        out[rid] = ConnectionTestStatus(
            success=row.get("success") is True,
            message=str(row.get("message") or ""),
            tested_at=tested_at.astimezone(timezone.utc),
            tested_by=row.get("tested_by_name") or None,
            duration_ms=int(row.get("duration_ms") or 0),
            stale=current - tested_at > STALE_AFTER,
        )
    return out


def _item_id(item: Any) -> Optional[str]:
    value = _field(item, "id")
    return value if isinstance(value, str) and value else None


def attach_last_tests(kind: str, items: Iterable[Any]) -> None:
    """Scrive ``last_test`` su ogni elemento (modello Pydantic o dict)."""
    materialized = list(items)
    statuses = last_connection_tests(
        kind, (rid for rid in map(_item_id, materialized) if rid),
    )
    for item in materialized:
        status = statuses.get(_item_id(item) or "")
        if isinstance(item, dict):
            item["last_test"] = status.model_dump(mode="json") if status else None
        else:
            setattr(item, "last_test", status)


def _wrap(func: Callable, around: Callable[[Callable, tuple, dict], Any]) -> Callable:
    if inspect.iscoroutinefunction(func):
        raise TypeError("connection_test_results: route asincrona non supportata")

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return around(func, args, kwargs)

    return wrapper


def records_connection_test(
    kind: str,
    id_param: str,
    *,
    when: Optional[Callable[[Mapping[str, Any]], bool]] = None,
) -> Callable[[Callable], Callable]:
    """Decoratore della route di test: registra l'esito della risposta."""
    _check_kind(kind)

    def decorator(func: Callable) -> Callable:
        def around(inner: Callable, args: tuple, kwargs: dict) -> Any:
            started_at = _now()
            started = time.monotonic()
            result = inner(*args, **kwargs)
            resource_id = kwargs.get(id_param)
            if isinstance(resource_id, str) and resource_id and (when is None or when(kwargs)):
                user_id, username = _user_of(kwargs)
                record_connection_test(
                    kind,
                    resource_id,
                    result,
                    started_at=started_at,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    user_id=user_id,
                    username=username,
                )
            return result

        wrapper = _wrap(func, around)
        setattr(wrapper, RECORDS_ATTR, (kind, id_param))
        return wrapper

    return decorator


def resets_connection_test(kind: str, id_param: str) -> Callable[[Callable], Callable]:
    """Decoratore di PUT/DELETE della connessione: a buon fine azzera l'esito."""
    _check_kind(kind)

    def decorator(func: Callable) -> Callable:
        def around(inner: Callable, args: tuple, kwargs: dict) -> Any:
            result = inner(*args, **kwargs)
            resource_id = kwargs.get(id_param)
            if isinstance(resource_id, str) and resource_id:
                reset_connection_test(kind, resource_id)
            return result

        wrapper = _wrap(func, around)
        setattr(wrapper, RESETS_ATTR, (kind, id_param))
        return wrapper

    return decorator


def attaches_connection_tests(kind: str, items_field: str) -> Callable[[Callable], Callable]:
    """Decoratore della route della lista: allega ``last_test`` a ogni elemento."""
    _check_kind(kind)

    def decorator(func: Callable) -> Callable:
        def around(inner: Callable, args: tuple, kwargs: dict) -> Any:
            result = inner(*args, **kwargs)
            items = _field(result, items_field)
            if isinstance(items, list):
                attach_last_tests(kind, items)
            return result

        wrapper = _wrap(func, around)
        setattr(wrapper, ATTACHES_ATTR, kind)
        return wrapper

    return decorator


__all__ = [
    "ATTACHES_ATTR",
    "CONNECTION_TEST_KINDS",
    "ConnectionTestRecord",
    "ConnectionTestStatus",
    "ConnectionTestStore",
    "MESSAGE_MAX_LEN",
    "PgConnectionTestStore",
    "RECORDS_ATTR",
    "RESETS_ATTR",
    "STALE_AFTER",
    "attach_last_tests",
    "attaches_connection_tests",
    "last_connection_tests",
    "outcome_of",
    "record_connection_test",
    "records_connection_test",
    "reset_connection_test",
    "resets_connection_test",
]
