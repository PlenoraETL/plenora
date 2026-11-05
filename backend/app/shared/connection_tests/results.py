"""
Esito del «Test connessione» delle connessioni salvate (Fase 5, voce S3).

REGOLA: ogni endpoint che testa una connessione SALVATA registra l'esito con
UNA sola funzione (``record_connection_test``, applicata dal decoratore
``records_connection_test`` sulla route): riuscito o no, messaggio senza
segreti, data/ora UTC, utente che l'ha eseguito, durata. Le liste delle connessioni portano l'ultimo esito
in ``last_test`` (decoratore ``attaches_connection_tests`` sulla route della
lista); un esito piu' vecchio di ``STALE_AFTER`` ha ``stale = true`` e
l'interfaccia lo mostra come «Da riverificare». Una modifica salvata o la
cancellazione della connessione azzera l'esito (decoratore
``resets_connection_test`` sulle route PUT/DELETE): la configurazione
testata non e' piu' quella salvata. Un test partito prima dell'azzeramento
non lo sovrascrive (``reset_at`` nella tabella).

Messaggio senza segreti, difesa a due livelli (``app/shared/error_sanitizer.py``),
entrambi sempre applicati da ``record_connection_test``:
 1. i segreti noti della risorsa testata (``secrets_of`` del decoratore,
    OBBLIGATORIO: password, token, chiavi, client secret, chiavi private,
    anche codificati in URL o in Base64 per Basic auth) sono sostituiti con
    ``[REDACTED]``; se i segreti non si possono leggere il messaggio salvato
    e' quello generico (``Test non riuscito`` / ``Connessione riuscita``);
 2. le regole generiche di ``sanitize_http_detail`` (URL, query string,
    coppie nome/valore sensibili, DSN, Authorization, ...).

Permessi: l'esito si registra solo quando la route del test risponde, cioe'
dopo i suoi controlli di visibilita'; si legge solo dentro le liste, che
mostrano le connessioni visibili all'utente. Il nome di chi ha eseguito il
test (``tested_by``) lo vede solo un superadmin o un utente dello STESSO
gruppo di chi l'ha eseguito (gruppo attuale, dalla tabella ``users``): e' la
stessa regola di ``users_router.list_users`` (l'admin vede solo gli utenti
del proprio gruppo). Agli altri ``tested_by = null`` (utente cancellato
compreso); l'interfaccia mostra «Verificata il <data>» senza nome.

Errori del registro (``_after_effect``, regola unica per scrittura):
 - registrazione e azzeramento avvengono DOPO un effetto gia' compiuto (test
   eseguito, connessione modificata o eliminata): QUALUNQUE errore (DB giu',
   SQL sbagliato, sanitizer, lettura dei segreti) si logga ERROR con il tipo
   e lo stack e NON arriva all'utente, che ha gia' l'esito della sua azione;
 - lettura (liste): fail-open (warning, nessun ``last_test``) SOLO per gli
   errori di disponibilita' (``_is_unavailable``: connessione/pool, tabella
   non ancora creata, DATABASE_URL assente); un errore di programmazione
   (SQL sbagliato, colonna inesistente) si logga ERROR e si propaga.

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
from app.shared.error_sanitizer import collect_secret_values, sanitize_with_secrets
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

# Il gruppo ATTUALE di chi ha testato decide chi ne vede il nome
# (``_tester_name_for``); utente cancellato -> gruppo NULL -> nome nascosto.
_LOAD_SQL = (
    "SELECT r.resource_id, r.success, r.message, r.tested_at, r.tested_by_name, "
    "r.duration_ms, u.group_id AS tested_by_group "
    "FROM connection_test_results r LEFT JOIN users u ON u.id = r.tested_by "
    "WHERE r.resource_kind = %s AND r.resource_id = ANY(%s) AND r.tested_at IS NOT NULL"
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

# Errori che la lettura (``load``) puo' sollevare. Di questi, solo quelli di
# DISPONIBILITA' (``_is_unavailable``) sono fail-open: un DB giu' non deve
# far fallire la lista. Gli altri (SQL sbagliato, colonna inesistente, uso
# errato della sessione) sono errori di programmazione: log ERROR e si
# propagano. Le scritture seguono invece ``_after_effect``.
_STORE_ERRORS = (psycopg.Error, RuntimeError)

# PoolTimeout/PoolClosed di psycopg_pool sono psycopg.OperationalError.
_UNAVAILABLE_DB_ERRORS = (
    psycopg.OperationalError,
    psycopg.InterfaceError,
    psycopg.errors.UndefinedTable,  # migrazione non ancora applicata
)
# RuntimeError di app.db.pg_connection quando DATABASE_URL non e' configurato.
_MISSING_DATABASE_URL = "DATABASE_URL"

# Errori della lettura dei segreti della risorsa (``secrets_of``): con uno di
# questi il messaggio salvato e' quello generico (fail-closed).
_SECRETS_ERRORS = (psycopg.Error, RuntimeError, ValueError, LookupError, OSError, TypeError)


def _is_unavailable(exc: BaseException) -> bool:
    if isinstance(exc, _UNAVAILABLE_DB_ERRORS):
        return True
    return isinstance(exc, RuntimeError) and _MISSING_DATABASE_URL in str(exc)


def _load_failed(kind: str, resource_id: str, exc: BaseException) -> None:
    """Lettura: fail-open per gli errori di disponibilita', altrimenti rilancia."""
    if _is_unavailable(exc):
        logger.warning(
            "connection_test_load_failed kind=%s id=%s: %s",
            kind, resource_id, type(exc).__name__,
        )
        return
    logger.error(
        "connection_test_load_error kind=%s id=%s: %s",
        kind, resource_id, type(exc).__name__,
    )
    raise exc


# Le scritture del registro che seguono un effetto gia' compiuto.
AFTER_EFFECT_OPS = ("record", "reset")


def _after_effect(op: str, kind: str, resource_id: str, action: Callable[[], None]) -> None:
    """Esegue ``action`` (scrittura del registro dopo un effetto compiuto):
    qualunque errore e' loggato ERROR con tipo e stack e non si propaga."""
    if op not in AFTER_EFFECT_OPS:
        raise ValueError(f"Operazione del registro sconosciuta: {op!r}")
    try:
        action()
    except Exception as exc:  # safety-net dichiarato: l'effetto e' gia' compiuto
        logger.error(
            "connection_test_%s_error kind=%s id=%s: %s",
            op, kind, resource_id, type(exc).__name__,
            exc_info=exc,
        )


def _check_kind(kind: str) -> None:
    if kind not in CONNECTION_TEST_KINDS:
        raise ValueError(f"Tipo di connessione senza test: {kind!r}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _field(result: Any, name: str) -> Any:
    if isinstance(result, Mapping):
        return result.get(name)
    return getattr(result, name, None)


def _fallback(success: bool, result: Any) -> str:
    raw = _SUCCESS_FALLBACK if success else _FAILURE_FALLBACK
    status_code = _field(result, "status_code")
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        raw = f"{raw} (HTTP {status_code})"
    return raw


def outcome_of(result: Any, secrets: Optional[Iterable[str]]) -> tuple[bool, str]:
    """Esito e messaggio senza segreti dalla risposta di un test.

    Le route rispondono con ``{success, message}`` (database, ArcGIS, Data
    Source) o ``{success, error}`` (connettori API). ``secrets``: i segreti
    noti della risorsa; ``None`` = non si sono potuti leggere -> messaggio
    generico (fail-closed).
    """
    success = _field(result, "success") is True
    raw = _field(result, "message") or _field(result, "error")
    if not raw or secrets is None:
        raw = _fallback(success, result)
    return success, sanitize_with_secrets(str(raw), secrets or (), max_len=MESSAGE_MAX_LEN)


def secrets_from(source: Any) -> frozenset[str]:
    """I segreti noti di una risorsa (config o modello con i valori in chiaro)."""
    return collect_secret_values(source)


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
    secrets: Optional[Iterable[str]],
) -> None:
    """LA funzione che registra l'esito di un test, dopo il test: qualunque
    errore (messaggio, DB) e' loggato e non si propaga (``_after_effect``).

    ``secrets``: i valori segreti della risorsa testata, oscurati nel
    messaggio; ``None`` se non si sono potuti leggere (messaggio generico).
    """
    _check_kind(kind)

    def write() -> None:
        success, message = outcome_of(result, secrets)
        _store.record(ConnectionTestRecord(
            kind=kind,
            resource_id=resource_id,
            success=success,
            message=message,
            tested_at=started_at,
            tested_by=user_id,
            tested_by_name=username,
            duration_ms=max(0, int(duration_ms)),
        ))

    _after_effect("record", kind, resource_id, write)


def reset_connection_test(kind: str, resource_id: str) -> None:
    """Azzera l'esito (modifica o cancellazione della connessione, gia'
    compiuta): qualunque errore e' loggato e non si propaga."""
    _check_kind(kind)
    _after_effect("reset", kind, resource_id, lambda: _store.reset(kind, resource_id, _now()))


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


def _tester_name_for(row: Mapping[str, Any], viewer: Any) -> Optional[str]:
    """Il nome di chi ha testato, se ``viewer`` lo puo' vedere: superadmin, o
    utente dello stesso gruppo (attuale) di chi ha testato."""
    name = row.get("tested_by_name") or None
    if not isinstance(name, str) or viewer is None:
        return None
    if getattr(viewer, "role", None) == "superadmin":
        return name
    viewer_group = getattr(viewer, "group_id", None)
    tester_group = row.get("tested_by_group")
    if isinstance(viewer_group, str) and viewer_group and viewer_group == tester_group:
        return name
    return None


def last_connection_tests(
    kind: str,
    resource_ids: Iterable[str],
    *,
    viewer: Any,
    now: Optional[datetime] = None,
) -> Mapping[str, ConnectionTestStatus]:
    """Ultimo esito per id (solo le connessioni testate dopo l'ultima modifica),
    visto da ``viewer`` (l'utente della richiesta: decide se vede il nome di
    chi ha testato)."""
    _check_kind(kind)
    ids = [rid for rid in dict.fromkeys(resource_ids) if isinstance(rid, str) and rid]
    if not ids:
        return {}
    try:
        rows = _store.load(kind, ids)
    except _STORE_ERRORS as exc:
        _load_failed(kind, ",".join(ids[:5]), exc)
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
            tested_by=_tester_name_for(row, viewer),
            duration_ms=int(row.get("duration_ms") or 0),
            stale=current - tested_at > STALE_AFTER,
        )
    return out


def _item_id(item: Any) -> Optional[str]:
    value = _field(item, "id")
    return value if isinstance(value, str) and value else None


def attach_last_tests(kind: str, items: Iterable[Any], *, viewer: Any) -> None:
    """Scrive ``last_test`` su ogni elemento (modello Pydantic o dict)."""
    materialized = list(items)
    statuses = last_connection_tests(
        kind, (rid for rid in map(_item_id, materialized) if rid), viewer=viewer,
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


def _secrets_of_resource(
    secrets_of: Callable[[str], Any], kind: str, resource_id: str,
) -> Optional[frozenset[str]]:
    """I segreti della risorsa, o ``None`` (messaggio generico) se non si
    possono leggere: errore di lettura o risorsa ``None`` (non trovata,
    credenziali non decifrabili)."""
    try:
        source = secrets_of(resource_id)
    except _SECRETS_ERRORS as exc:
        logger.warning(
            "connection_test_secrets_unavailable kind=%s id=%s: %s",
            kind, resource_id, type(exc).__name__,
        )
        return None
    if source is None:
        return None
    return secrets_from(source)


def _record_response(
    kind: str,
    resource_id: str,
    result: Any,
    kwargs: Mapping[str, Any],
    *,
    secrets_of: Callable[[str], Any],
    when: Optional[Callable[[Mapping[str, Any]], bool]],
    started_at: datetime,
    duration_ms: int,
) -> None:
    """Registra la risposta di una route di test (``records_connection_test``)."""
    if when is not None and not when(kwargs):
        return
    user_id, username = _user_of(kwargs)
    record_connection_test(
        kind,
        resource_id,
        result,
        started_at=started_at,
        duration_ms=duration_ms,
        user_id=user_id,
        username=username,
        secrets=_secrets_of_resource(secrets_of, kind, resource_id),
    )


def records_connection_test(
    kind: str,
    id_param: str,
    *,
    secrets_of: Callable[[str], Any],
    when: Optional[Callable[[Mapping[str, Any]], bool]] = None,
) -> Callable[[Callable], Callable]:
    """Decoratore della route di test: registra l'esito della risposta.

    ``secrets_of(resource_id)``: la risorsa con i segreti IN CHIARO (config o
    modello), da cui ``collect_secret_values`` ricava i valori da oscurare;
    ``None`` se non e' leggibile (il messaggio salvato sara' quello generico).
    """
    _check_kind(kind)
    if not callable(secrets_of):
        raise TypeError("records_connection_test: secrets_of obbligatorio")

    def decorator(func: Callable) -> Callable:
        def around(inner: Callable, args: tuple, kwargs: dict) -> Any:
            started_at = _now()
            started = time.monotonic()
            result = inner(*args, **kwargs)
            resource_id = kwargs.get(id_param)
            if isinstance(resource_id, str) and resource_id:
                duration_ms = round((time.monotonic() - started) * 1000)
                # Il test e' gia' eseguito: anche ``when`` e la lettura dei
                # segreti stanno sotto la regola di ``_after_effect``.
                _after_effect("record", kind, resource_id, lambda: _record_response(
                    kind, resource_id, result, kwargs, secrets_of=secrets_of, when=when,
                    started_at=started_at, duration_ms=duration_ms,
                ))
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
                attach_last_tests(kind, items, viewer=kwargs.get("current_user"))
            return result

        wrapper = _wrap(func, around)
        setattr(wrapper, ATTACHES_ATTR, kind)
        return wrapper

    return decorator


__all__ = [
    "AFTER_EFFECT_OPS",
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
    "secrets_from",
]
