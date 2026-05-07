"""
Decorator `@audit_action` per audit trail automatico su endpoint sensibili.

Wrappa funzioni router FastAPI (sync o async): al termine della request
"""

from __future__ import annotations

import functools
import inspect
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import HTTPException

from app.utils.logger import get_logger

logger = get_logger(__name__)


# METRICHE (contatori in-process, pattern token_blacklist)
#
# Counters sincroni protetti da lock. Azzerati a ogni restart del backend.
# Esposti via get_metrics() per integrazione futura con admin_system_status.

_metrics_lock = threading.Lock()
_metrics: Dict[str, int] = {
    "write_ok":   0,  # INSERT su plenora.audit_actions riuscito
    "write_fail": 0,  # INSERT sollevato (DB down, pool exhausted, ...)
    "skipped":    0,  # chiamate dove il decorator non ha tentato la write
                      # (reservato per ramo opt-out futuro; oggi sempre 0)
}
_last_ok_epoch: Optional[float] = None


def _record_metric(name: str, mark_ok: bool = False) -> None:
    """Incrementa un contatore, opzionalmente aggiorna `last_ok_epoch`."""
    global _last_ok_epoch
    with _metrics_lock:
        _metrics[name] = _metrics.get(name, 0) + 1
        if mark_ok:
            _last_ok_epoch = time.time()


def get_metrics() -> Dict[str, Any]:
    """Snapshot contatori + timestamp ultima write riuscita.

    Mirror del pattern `token_blacklist.get_metrics()`; consumabile da
    una dashboard admin o da uno script di health probe.
    """
    with _metrics_lock:
        return {
            "counters": dict(_metrics),
            "last_ok_epoch": _last_ok_epoch,
        }


def _reset_metrics_for_tests() -> None:
    """Test-only: azzera contatori e `last_ok_epoch` per isolare i test."""
    global _last_ok_epoch
    with _metrics_lock:
        for k in list(_metrics.keys()):
            _metrics[k] = 0
        _last_ok_epoch = None


# EXTRACTORS DAL KWARGS DELLA FUNZIONE WRAPPATA


def _extract_user_id(kwargs: Dict[str, Any]) -> Optional[str]:
    """Legge `current_user.user_id` da kwargs se presente e non vuoto.

    None (anonimo/sistema) se:
      - `current_user` non e' passato come kwarg (endpoint pubblico o
        decorator applicato a task di background senza dipendenza user)
      - `current_user` e' None esplicito
      - `current_user.user_id` non e' una stringa non vuota
    """
    cu = kwargs.get("current_user")
    if cu is None:
        return None
    uid = getattr(cu, "user_id", None)
    if isinstance(uid, str) and uid:
        return uid
    return None


def _extract_ip_address(kwargs: Dict[str, Any]) -> Optional[str]:
    """Legge `request.client.host` da kwargs se `request: Request` e' presente.

    None se:
      - `request` non e' passato come kwarg
      - `request.client` e' None (es. TestClient in modalita' particolari)
      - il client.host non e' una stringa
    """
    req = kwargs.get("request")
    if req is None:
        return None
    client = getattr(req, "client", None)
    if client is None:
        return None
    host = getattr(client, "host", None)
    if isinstance(host, str) and host:
        return host
    return None


def _extract_target(
    kwargs: Dict[str, Any], target_param: Optional[str]
) -> Optional[str]:
    """Estrae il target dal kwarg specificato dal decorator.

    Accetta solo str/int (convertito a str). Pydantic model, dict o altri
    oggetti → None (no serializzazione su valori arbitrari per evitare
    payload abuse sulla colonna TEXT).
    """
    if target_param is None:
        return None
    val = kwargs.get(target_param)
    if val is None:
        return None
    if isinstance(val, (str, int)):
        return str(val)
    return None


def _short_error(msg: Any, limit: int = 500) -> Optional[str]:
    """Tronca il messaggio d'errore per evitare row giganti in audit.

    Ritorna None se `msg` e' None, altrimenti la stringa troncata.
    """
    if msg is None:
        return None
    s = str(msg)
    if len(s) > limit:
        return s[:limit]
    return s


# WRITE FAIL-OPEN

_INSERT_SQL = (
    "INSERT INTO plenora.audit_actions "
    "(user_id, action, target, status, ip_address, error_message) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)


def _execute_write(sql: str, params: Tuple[Any, ...]) -> None:
    """Indirection layer con lazy import di `pg_connection`.

    Isolato in funzione dedicata cosi' i test possono monkeypatch-are
    il write punto senza triggerare il check `DATABASE_URL` che
    `app.db.pg_connection` esegue al top-level. Pattern coerente con
    `token_blacklist._get_client()` (lazy import interna a funzione).
    """
    from app.db import pg_connection

    pg_connection.execute(sql, params)


def _write_audit_row(
    user_id: Optional[str],
    action: str,
    target: Optional[str],
    status: int,
    ip_address: Optional[str],
    error_message: Optional[str],
) -> None:
    """Inserisce la riga audit. Fail-open: logga warning e prosegue.

    Contratto: non solleva mai. Il caller (decorator) usa questa funzione
    dentro il finally-equivalente del flusso: la request business ha
    gia' prodotto il suo risultato o la sua eccezione, l'audit e' best
    effort.
    """
    try:
        _execute_write(
            _INSERT_SQL,
            (user_id, action, target, status, ip_address, error_message),
        )
        _record_metric("write_ok", mark_ok=True)
    except Exception as exc:
        # Fail-open: mai far saltare la request business per un problema
        # sulla write di audit. Il warning e' il segnale osservabile:
        # una cascata di `audit_action_write_failed` su piu' endpoint
        # indica PG pool exhaustion / down / schema drift.
        logger.warning(
            "audit_action_write_failed action=%s user_id=%s status=%s: %s: %s",
            action,
            user_id if user_id is not None else "<anon>",
            status,
            type(exc).__name__,
            exc,
        )
        _record_metric("write_fail")


# DECORATOR


def audit_action(
    action: str, target_param: Optional[str] = None
) -> Callable[[Callable], Callable]:
    """Decorator per audit trail automatico. Vedi docstring modulo.

    Args:
        action: nome azione namespace punto-separato. Es. 'project.delete'.
        target_param: nome del kwarg della funzione wrappata da cui leggere
            l'id della risorsa target (str o int). None se l'azione non
            ha un target identificabile.

    Ritorna il decorator effettivo, che wrappa sia funzioni sync che async
    preservando la firma originale (via `functools.wraps`).
    """
    if not isinstance(action, str) or not action:
        raise TypeError("audit_action: 'action' deve essere una stringa non vuota")
    if target_param is not None and not isinstance(target_param, str):
        raise TypeError("audit_action: 'target_param' deve essere None o str")

    def decorator(func: Callable) -> Callable:
        is_coro = inspect.iscoroutinefunction(func)

        if is_coro:

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                user_id = _extract_user_id(kwargs)
                ip_address = _extract_ip_address(kwargs)
                target = _extract_target(kwargs, target_param)
                try:
                    result = await func(*args, **kwargs)
                    _write_audit_row(user_id, action, target, 200, ip_address, None)
                    return result
                except HTTPException as exc:
                    _write_audit_row(
                        user_id,
                        action,
                        target,
                        exc.status_code,
                        ip_address,
                        _short_error(exc.detail),
                    )
                    raise
                except Exception as exc:
                    _write_audit_row(
                        user_id,
                        action,
                        target,
                        500,
                        ip_address,
                        _short_error(f"{type(exc).__name__}: {exc}"),
                    )
                    raise

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            user_id = _extract_user_id(kwargs)
            ip_address = _extract_ip_address(kwargs)
            target = _extract_target(kwargs, target_param)
            try:
                result = func(*args, **kwargs)
                _write_audit_row(user_id, action, target, 200, ip_address, None)
                return result
            except HTTPException as exc:
                _write_audit_row(
                    user_id,
                    action,
                    target,
                    exc.status_code,
                    ip_address,
                    _short_error(exc.detail),
                )
                raise
            except Exception as exc:
                _write_audit_row(
                    user_id,
                    action,
                    target,
                    500,
                    ip_address,
                    _short_error(f"{type(exc).__name__}: {exc}"),
                )
                raise

        return sync_wrapper

    return decorator
