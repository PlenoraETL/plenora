"""Persistenza stato/esito task su PostgreSQL.

Lo stato corrente, il progress e il risultato finale di ogni task vengono
scritti dai worker keyed-by-task_id e letti dall'orchestratore via
``TaskRuntimePort.get_result()``.

Forma del record: una sola ``payload`` riproduce la semantica Celery
``AsyncResult.info == .result``. Durante ``PROGRESS`` la payload e' il
dict di progresso ``{progress, message, is_error}``; sugli stati
terminali (``SUCCESS``/``FAILURE``) e' il dict di ritorno del task.

TTL: ``expires_at`` definisce la scadenza dei record; ``cleanup_expired``
e' il sweep di manutenzione.
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Protocol

from app.db import pg_connection
from app.db.pg_connection import unit_of_work
from app.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_TTL_SECONDS = 24 * 60 * 60

# Stato di un task non presente nello store: PENDING, niente info.
PENDING_STATE = "PENDING"


def _normal_ttl(ttl_seconds: int | None) -> int:
    if ttl_seconds is None or ttl_seconds <= 0:
        return _DEFAULT_TTL_SECONDS
    return ttl_seconds


def _compact_decimal(value: Decimal) -> str:
    if not value.is_finite():
        return str(value)
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _json_safe_payload(value: object) -> object:
    """Normalize task payload values before JSONB persistence."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, Decimal):
        return _compact_decimal(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe_payload(nested_value)
            for key, nested_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_payload(nested_value) for nested_value in value]

    type_name = type(value).__name__
    if type_name in {"NaT", "NaTType"}:
        return None

    item = getattr(value, "item", None)
    if callable(item):
        try:
            item_value = item()
        except (TypeError, ValueError):
            item_value = value
        if item_value is not value:
            return _json_safe_payload(item_value)

    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return str(isoformat())
        except (TypeError, ValueError):
            pass

    return str(value)


@dataclass(frozen=True)
class TaskResultRecord:
    """Riga di stato/esito di un task.

    ``payload`` segue la semantica Celery info==result (vedi modulo).
    """

    task_id: str
    state: str
    name: Optional[str] = None
    payload: Optional[dict] = None


class TaskResultStore(Protocol):
    """Porta di persistenza per lo stato/esito dei task."""

    def put(
        self,
        task_id: str,
        *,
        state: str,
        payload: Optional[dict] = None,
        name: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        ...

    def get(self, task_id: str) -> Optional[TaskResultRecord]:
        ...

    def forget(self, task_id: str) -> None:
        ...

    def cleanup_expired(self) -> int:
        ...

    def count_in_state(self, state: str) -> int:
        ...


class PgTaskResultStore:
    """Backend PostgreSQL dello store."""

    def __init__(self, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._ttl_seconds = _normal_ttl(ttl_seconds)

    def put(
        self,
        task_id: str,
        *,
        state: str,
        payload: Optional[dict] = None,
        name: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        ttl = _normal_ttl(ttl_seconds) if ttl_seconds is not None else self._ttl_seconds
        payload_json = (
            json.dumps(
                _json_safe_payload(payload),
                separators=(",", ":"),
                allow_nan=False,
            )
            if payload is not None
            else None
        )
        # Scrittura via pg_connection.execute (non unit_of_work diretto): e'
        # il path che i task usano a runtime, e l'autouse della lane
        # (_stub_pg_connection_execute) lo no-oppa nei unit test → niente
        # accesso DB reale quando un task viene eseguito in unit.
        # name via COALESCE(EXCLUDED, esistente): una write di progress
        # successiva che non porta il nome non lo azzera.
        pg_connection.execute(
            """
            INSERT INTO task_results (
                task_id, state, name, payload,
                created_at, updated_at, expires_at
            )
            VALUES (
                %s, %s, %s, %s::jsonb,
                now(), now(), now() + (%s * interval '1 second')
            )
            ON CONFLICT (task_id) DO UPDATE
            SET state = EXCLUDED.state,
                name = COALESCE(EXCLUDED.name, task_results.name),
                payload = EXCLUDED.payload,
                updated_at = now(),
                expires_at = EXCLUDED.expires_at
            """,
            (task_id, state, name, payload_json, ttl),
        )

    def put_terminal_with_run(
        self,
        task_id: str,
        *,
        state: str,
        payload: dict,
        name: Optional[str],
    ) -> None:
        """Atomically persist task state and its correlated pipeline run."""
        run_id = str(payload.get("run_id") or "")
        if not run_id:
            self.put(task_id, state=state, payload=payload, name=name)
            return
        payload_json = json.dumps(
            _json_safe_payload(payload),
            separators=(",", ":"),
            allow_nan=False,
        )
        run_status = {
            "SUCCESS": "success",
            "FAILURE": "failure",
            "REVOKED": "revoked",
        }.get(state, state.lower())
        with unit_of_work() as session:
            session.execute(
                """
                INSERT INTO task_results (
                    task_id, state, name, payload,
                    created_at, updated_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s::jsonb,
                    now(), now(), now() + (%s * interval '1 second')
                )
                ON CONFLICT (task_id) DO UPDATE
                SET state = EXCLUDED.state,
                    name = COALESCE(EXCLUDED.name, task_results.name),
                    payload = EXCLUDED.payload,
                    updated_at = now(),
                    expires_at = EXCLUDED.expires_at
                """,
                (task_id, state, name, payload_json, self._ttl_seconds),
            )
            session.execute(
                "UPDATE log_runs SET status = %s, "
                "ts_end = COALESCE(ts_end, now()) WHERE run_id = %s",
                (run_status, run_id),
            )

    def get(self, task_id: str) -> Optional[TaskResultRecord]:
        with unit_of_work() as session:
            row = session.fetchone(
                "SELECT task_id, state, name, payload FROM task_results "
                "WHERE task_id = %s "
                "AND (expires_at IS NULL OR expires_at > now())",
                (task_id,),
            )
        if not row:
            return None
        payload = row["payload"]
        # psycopg3 deserializza JSONB in dict; difensivo se arrivasse str.
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                logger.warning("payload task_results non valido task_id=%s", task_id)
                payload = None
        return TaskResultRecord(
            task_id=str(row["task_id"]),
            state=str(row["state"]),
            name=row["name"],
            payload=payload if isinstance(payload, dict) else None,
        )

    def forget(self, task_id: str) -> None:
        pg_connection.execute(
            "DELETE FROM task_results WHERE task_id = %s", (task_id,)
        )

    def cleanup_expired(self) -> int:
        cur = pg_connection.execute(
            "DELETE FROM task_results "
            "WHERE expires_at IS NOT NULL AND expires_at <= now()"
        )
        return int(getattr(cur, "rowcount", 0) or 0)

    def count_in_state(self, state: str) -> int:
        """Conta i task non scaduti in uno stato (es. PROGRESS = in corso).

        Usato dall'osservabilita' admin derivata da PostgreSQL."""
        with unit_of_work() as session:
            row = session.fetchone(
                "SELECT count(*) AS n FROM task_results "
                "WHERE state = %s "
                "AND (expires_at IS NULL OR expires_at > now())",
                (state,),
            )
        return int(row["n"]) if row else 0


class InMemoryTaskResultStore:
    """Test double process-local (lane no-DB) e fallback.

    Niente scadenza temporale reale: i record restano finche' non
    sovrascritti/dimenticati. La semantica TTL del backend PG e' coperta
    a livello SQL (``expires_at``) ed e2e sullo stack."""

    def __init__(self) -> None:
        self._by_task_id: dict[str, TaskResultRecord] = {}
        self._lock = threading.Lock()

    def put(
        self,
        task_id: str,
        *,
        state: str,
        payload: Optional[dict] = None,
        name: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        with self._lock:
            existing = self._by_task_id.get(task_id)
            # Parita' col COALESCE PG: un put senza name non cancella il
            # nome gia' registrato.
            resolved_name = name if name is not None else (
                existing.name if existing is not None else None
            )
            self._by_task_id[task_id] = TaskResultRecord(
                task_id=task_id,
                state=state,
                name=resolved_name,
                payload=(
                    _json_safe_payload(payload) if payload is not None else None
                ),
            )

    def get(self, task_id: str) -> Optional[TaskResultRecord]:
        with self._lock:
            return self._by_task_id.get(task_id)

    def forget(self, task_id: str) -> None:
        with self._lock:
            self._by_task_id.pop(task_id, None)

    def cleanup_expired(self) -> int:
        # Senza TTL temporale in-memory non c'e' nulla da scadere.
        return 0

    def count_in_state(self, state: str) -> int:
        with self._lock:
            return sum(1 for r in self._by_task_id.values() if r.state == state)


def default_task_result_store() -> TaskResultStore:
    """Costruisce lo store di default (PostgreSQL) dalle settings."""
    from app.config import settings

    return PgTaskResultStore(ttl_seconds=settings.TASK_RESULT_EXPIRES)


__all__ = [
    "InMemoryTaskResultStore",
    "PENDING_STATE",
    "PgTaskResultStore",
    "TaskResultRecord",
    "TaskResultStore",
    "default_task_result_store",
]
