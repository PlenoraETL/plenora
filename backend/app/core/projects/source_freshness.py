"""Freshness checks for cross-project source references."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.projects import source_freshness_store

ProjectSourceFreshnessPayload = dict[str, Any]


@dataclass(frozen=True)
class ProjectSourceFreshness:
    source_project_id: str
    source_project_name: str | None
    project_updated_at: datetime | None
    latest_result_at: datetime | None
    stale: bool
    reason: str

    @property
    def message(self) -> str:
        name = self.source_project_name or self.source_project_id
        if self.reason == "missing_result":
            return (
                f"Il progetto sorgente '{name}' non ha un risultato disponibile. "
                "Esegui prima il progetto sorgente."
            )
        if self.reason == "canvas_newer_than_result":
            return (
                f"Il progetto sorgente '{name}' e' stato modificato dopo "
                "l'ultima esecuzione. Esegui prima il progetto sorgente "
                "o rilancia con ricalcolo forzato."
            )
        return f"Il progetto sorgente '{name}' e' allineato."

    def to_payload(self) -> ProjectSourceFreshnessPayload:
        return {
            "source_project_id": self.source_project_id,
            "source_project_name": self.source_project_name,
            "project_updated_at": _serialize_dt(self.project_updated_at),
            "latest_result_at": _serialize_dt(self.latest_result_at),
            "stale": self.stale,
            "reason": self.reason,
            "message": self.message,
        }


class ProjectSourceStaleError(ValueError):
    def __init__(self, freshness: ProjectSourceFreshness) -> None:
        self.freshness = freshness
        super().__init__(freshness.message)


def _serialize_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _coerce_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def get_project_source_freshness(
    source_project_id: str,
    *,
    expected_group_id: str | None = None,
) -> ProjectSourceFreshness | None:
    """Return whether a project_source points at stale materialized data.

    ``projects.updated_at`` is intentionally treated as the editor/canvas
    timestamp. Runtime output_contract updates must not touch it.
    """
    if not source_project_id:
        return None

    row = source_freshness_store.fetch_project_source_freshness_row(
        source_project_id,
        expected_group_id=expected_group_id,
    )

    if not row:
        return None

    project_updated_at = _coerce_dt(row.get("project_updated_at"))
    latest_result_at = _coerce_dt(row.get("latest_result_at"))

    stale = False
    reason = "fresh"
    if latest_result_at is None:
        stale = True
        reason = "missing_result"
    elif project_updated_at and project_updated_at > latest_result_at:
        stale = True
        reason = "canvas_newer_than_result"

    return ProjectSourceFreshness(
        source_project_id=str(row.get("id") or source_project_id),
        source_project_name=str(row.get("name")) if row.get("name") else None,
        project_updated_at=project_updated_at,
        latest_result_at=latest_result_at,
        stale=stale,
        reason=reason,
    )


def assert_project_source_fresh(
    source_project_id: str,
    *,
    expected_group_id: str | None = None,
) -> None:
    freshness = get_project_source_freshness(
        source_project_id,
        expected_group_id=expected_group_id,
    )
    if freshness and freshness.stale:
        raise ProjectSourceStaleError(freshness)


def _node_source_project_id(node: Any) -> str | None:
    value = getattr(node, "source_project_id", None)
    if value:
        return str(value)
    data = getattr(node, "data", None)
    if isinstance(data, dict) and data.get("source_project_id"):
        return str(data["source_project_id"])
    return None


def annotate_canvas_project_source_freshness(
    canvas: Any,
    *,
    expected_group_id: str | None = None,
) -> bool:
    """Attach transient freshness payloads to project_source nodes."""
    changed = False
    for node in getattr(canvas, "nodes", []) or []:
        if getattr(node, "type", None) != "project_source":
            continue
        source_project_id = _node_source_project_id(node)
        if not source_project_id:
            continue
        freshness = get_project_source_freshness(
            source_project_id,
            expected_group_id=expected_group_id,
        )
        payload = freshness.to_payload() if freshness else None
        if getattr(node, "source_freshness", None) != payload:
            setattr(node, "source_freshness", payload)
            changed = True
        if getattr(node, "data", None) is None:
            node.data = {}
        if isinstance(node.data, dict) and node.data.get("source_freshness") != payload:
            node.data["source_freshness"] = payload
            changed = True
    return changed


def clear_canvas_project_source_freshness(canvas: Any) -> None:
    """Remove transient freshness payloads before persisting editor state."""
    for node in getattr(canvas, "nodes", []) or []:
        if getattr(node, "type", None) != "project_source":
            continue
        if hasattr(node, "source_freshness"):
            setattr(node, "source_freshness", None)
        data = getattr(node, "data", None)
        if isinstance(data, dict):
            data.pop("source_freshness", None)
