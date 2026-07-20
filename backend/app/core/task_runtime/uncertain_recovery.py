"""Operator-controlled recovery for CARICA writes with an uncertain outcome."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.db.pg_connection import unit_of_work


UncertainResolution = Literal["confirm_completed", "allow_retry"]


class UncertainRunNotFound(LookupError):
    """The requested intermediate run does not exist."""


class UncertainRunConflict(RuntimeError):
    """The run is not currently eligible for uncertain-outcome recovery."""


@dataclass(frozen=True, slots=True)
class UncertainCaricaRun:
    run_id: str
    project_id: str
    group_id: str
    timestamp: datetime
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class UncertainResolutionOutcome:
    run_id: str
    previous_status: str
    status: str
    resolution: UncertainResolution


def list_uncertain_carica_runs(*, limit: int = 100) -> list[UncertainCaricaRun]:
    """List a bounded set of explicit CARICA uncertainty barriers."""
    bounded_limit = max(1, min(int(limit), 500))
    with unit_of_work() as session:
        rows = session.fetchall(
            "SELECT r.run_id, r.canvas_id AS project_id, r.group_id, "
            "r.timestamp, l.error_message "
            "FROM runs AS r LEFT JOIN log_runs AS l ON l.run_id = r.run_id "
            "WHERE r.status = 'completion_uncertain' "
            "AND (l.module = 'carica' OR l.module IS NULL) "
            "ORDER BY r.timestamp ASC LIMIT %s",
            (bounded_limit,),
        )
    return [
        UncertainCaricaRun(
            run_id=str(row["run_id"]),
            project_id=str(row["project_id"]),
            group_id=str(row["group_id"]),
            timestamp=row["timestamp"],
            error_message=(
                str(row["error_message"]) if row.get("error_message") else None
            ),
        )
        for row in rows
    ]


def resolve_uncertain_carica_run(
    *,
    run_id: str,
    resolution: UncertainResolution,
    operator_user_id: str,
    note: str,
) -> UncertainResolutionOutcome:
    """Resolve an uncertainty barrier after the operator checks the target.

    ``confirm_completed`` closes the idempotency barrier as completed.
    ``allow_retry`` marks it failed, which explicitly authorizes a later retry.
    The compare-and-set update makes concurrent or repeated decisions safe.
    """
    clean_run_id = str(run_id or "").strip()
    clean_operator = str(operator_user_id or "").strip()
    clean_note = str(note or "").strip()
    if not clean_run_id:
        raise ValueError("run_id obbligatorio")
    if not clean_operator:
        raise ValueError("operator_user_id obbligatorio")
    if len(clean_note) < 3:
        raise ValueError("note deve contenere almeno 3 caratteri")
    if resolution not in {"confirm_completed", "allow_retry"}:
        raise ValueError("resolution non valida")

    target_status = "completed" if resolution == "confirm_completed" else "failed"
    resolved_at = datetime.now(timezone.utc)

    with unit_of_work() as session:
        row = session.fetchone(
            "SELECT r.run_id, r.status, l.module, l.error_message "
            "FROM runs AS r LEFT JOIN log_runs AS l ON l.run_id = r.run_id "
            "WHERE r.run_id = %s FOR UPDATE OF r",
            (clean_run_id,),
        )
        if row is None:
            raise UncertainRunNotFound(clean_run_id)
        previous_status = str(row["status"] or "")
        if previous_status != "completion_uncertain":
            raise UncertainRunConflict(
                f"run {clean_run_id} in stato {previous_status or '<vuoto>'}"
            )
        module = str(row.get("module") or "")
        if module and module != "carica":
            raise UncertainRunConflict(
                f"run {clean_run_id} non appartiene al modulo CARICA"
            )

        audit_payload = json.dumps(
            {
                "resolution": resolution,
                "operator_user_id": clean_operator,
                "note": clean_note,
                "resolved_at": resolved_at.isoformat(),
                "previous_error": row.get("error_message"),
            },
            separators=(",", ":"),
        )
        updated = session.execute(
            "UPDATE runs SET status = %s WHERE run_id = %s "
            "AND status = 'completion_uncertain'",
            (target_status, clean_run_id),
        )
        if int(getattr(updated, "rowcount", 0) or 0) != 1:
            raise UncertainRunConflict(
                f"run {clean_run_id} modificata da un'altra operazione"
            )
        session.execute(
            "UPDATE log_runs SET status = %s, ts_end = COALESCE(ts_end, now()), "
            "debug_payload = COALESCE(debug_payload, '{}'::jsonb) || "
            "jsonb_build_object('uncertain_resolution', %s::jsonb), "
            "error_module = CASE WHEN %s = 'success' THEN NULL ELSE error_module END, "
            "error_message = CASE WHEN %s = 'success' THEN NULL ELSE error_message END "
            "WHERE run_id = %s",
            (
                "success" if target_status == "completed" else "failure",
                audit_payload,
                "success" if target_status == "completed" else "failure",
                "success" if target_status == "completed" else "failure",
                clean_run_id,
            ),
        )

    return UncertainResolutionOutcome(
        run_id=clean_run_id,
        previous_status=previous_status,
        status=target_status,
        resolution=resolution,
    )


__all__ = [
    "UncertainCaricaRun",
    "UncertainResolution",
    "UncertainResolutionOutcome",
    "UncertainRunConflict",
    "UncertainRunNotFound",
    "list_uncertain_carica_runs",
    "resolve_uncertain_carica_run",
]
