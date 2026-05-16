"""Run persistence boundary for intermediate executions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, TypeAlias

from app.db.pg_connection import execute, fetchall, fetchone

RunRowPayload: TypeAlias = dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunRow:
    """Typed representation of a row from intermediate.runs."""

    run_id: Optional[str] = None
    canvas_id: Optional[str] = None
    group_id: Optional[str] = None
    timestamp: Any = None
    status: Optional[str] = None
    pinned: bool = False
    total_rows_in: Optional[int] = None
    total_rows_out: Optional[int] = None
    total_rows_dropped: Optional[int] = None
    total_nodes: Optional[int] = None
    nodes_ok: Optional[int] = None
    nodes_error: Optional[int] = None
    nodes_skipped: Optional[int] = None
    total_exec_time: Optional[float] = None
    input_hash: Optional[str] = None
    output_hash: Optional[str] = None
    user_id: Optional[str] = None
    _extra: Mapping[str, Any] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _present_fields: frozenset[str] = field(
        default_factory=frozenset,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "pinned", bool(self.pinned))
        object.__setattr__(self, "_extra", MappingProxyType(dict(self._extra)))
        present = self._present_fields or frozenset(_RUN_ROW_FIELD_NAMES)
        object.__setattr__(self, "_present_fields", frozenset(present))

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | "RunRow") -> "RunRow":
        if isinstance(record, cls):
            return record

        data = dict(record)
        kwargs = {
            field_name: data[field_name]
            for field_name in _RUN_ROW_FIELD_NAMES
            if field_name in data
        }
        extra = {
            key: value
            for key, value in data.items()
            if key not in _RUN_ROW_FIELD_NAMES
        }
        return cls(
            **kwargs,
            _extra=extra,
            _present_fields=frozenset(data.keys()),
        )

    def as_dict(self) -> RunRowPayload:
        row: RunRowPayload = {
            field_name: getattr(self, field_name)
            for field_name in _RUN_ROW_FIELD_NAMES
            if field_name in self._present_fields
        }
        for key, value in self._extra.items():
            if key in self._present_fields:
                row[key] = value
        return row

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


_RUN_ROW_FIELD_NAMES = (
    "run_id",
    "canvas_id",
    "group_id",
    "timestamp",
    "status",
    "pinned",
    "total_rows_in",
    "total_rows_out",
    "total_rows_dropped",
    "total_nodes",
    "nodes_ok",
    "nodes_error",
    "nodes_skipped",
    "total_exec_time",
    "input_hash",
    "output_hash",
    "user_id",
)

RunRecord = Mapping[str, Any] | RunRow
FetchAll = Callable[[str, tuple], list[Mapping[str, Any]]]
FetchOne = Callable[[str, tuple], Optional[Mapping[str, Any]]]
Execute = Callable[[str, tuple], object]
GetRun = Callable[[str], Optional[RunRecord]]
_ALLOWED_RUN_UPDATE_FIELDS = {
    "status",
    "input_hash", "output_hash",
    "total_exec_time",
    "total_rows_in", "total_rows_out", "total_rows_dropped",
    "total_nodes", "nodes_ok", "nodes_error", "nodes_skipped",
}


def create_run(
    run_id: str,
    canvas_id: str,
    group_id: str,
    total_nodes: int = 0,
    user_id: Optional[str] = None,
    *,
    execute_func: Execute | None = None,
    get_run_func: GetRun | None = None,
) -> Optional[RunRow]:
    """Create a new row in intermediate.runs."""
    execute_boundary = execute_func or execute
    get_run_boundary = get_run_func or get_run
    now = datetime.now(timezone.utc).isoformat()
    execute_boundary(
        """INSERT INTO runs
           (run_id, canvas_id, group_id, timestamp, status, total_nodes, user_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (run_id, canvas_id, group_id, now, "running", total_nodes, user_id),
    )
    return _hydrate_run(get_run_boundary(run_id))


def get_run(
    run_id: str,
    *,
    fetchone_func: FetchOne | None = None,
) -> Optional[RunRow]:
    fetchone_boundary = fetchone_func or fetchone
    return _hydrate_run(
        fetchone_boundary("SELECT * FROM runs WHERE run_id = %s", (run_id,))
    )


def list_runs(
    canvas_id: str,
    group_id: Optional[str],
    limit: int = 50,
    *,
    fetchall_func: FetchAll | None = None,
) -> list[RunRow]:
    """List runs for a canvas, isolated by group, newest first."""
    fetchall_boundary = fetchall_func or fetchall
    if group_id is None:
        rows = fetchall_boundary(
            "SELECT * FROM runs WHERE canvas_id = %s ORDER BY timestamp DESC LIMIT %s",
            (canvas_id, limit),
        )
    else:
        rows = fetchall_boundary(
            "SELECT * FROM runs WHERE canvas_id = %s AND group_id = %s ORDER BY timestamp DESC LIMIT %s",
            (canvas_id, group_id, limit),
        )
    return [RunRow.from_record(row) for row in rows]


def _hydrate_run(record: Optional[RunRecord]) -> Optional[RunRow]:
    if record is None:
        return None
    return RunRow.from_record(record)


def update_run(
    run_id: str,
    *,
    execute_func: Execute | None = None,
    **fields,
) -> None:
    """Update allowed mutable fields for a run."""
    if not fields:
        return
    invalid = set(fields) - _ALLOWED_RUN_UPDATE_FIELDS
    if invalid:
        raise ValueError(
            f"Campi non consentiti per UPDATE runs: {invalid}. "
            f"Consentiti: {_ALLOWED_RUN_UPDATE_FIELDS}"
        )
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    execute_boundary = execute_func or execute
    execute_boundary(
        f"UPDATE runs SET {set_clause} WHERE run_id = %s",
        tuple(fields.values()) + (run_id,),
    )


def pin_run(
    run_id: str,
    group_id: Optional[str],
    pinned: bool = True,
    *,
    execute_func: Execute | None = None,
) -> bool:
    """Pin or unpin a run, optionally scoped by group."""
    execute_boundary = execute_func or execute
    if group_id is None:
        cur = execute_boundary(
            "UPDATE runs SET pinned = %s WHERE run_id = %s",
            (pinned, run_id),
        )
    else:
        cur = execute_boundary(
            "UPDATE runs SET pinned = %s WHERE run_id = %s AND group_id = %s",
            (pinned, run_id, group_id),
        )
    return cur.rowcount > 0
