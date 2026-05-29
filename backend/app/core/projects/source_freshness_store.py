"""DB boundary for project-source freshness checks."""

from __future__ import annotations

from typing import Any, Mapping

from app.db.pg_connection import unit_of_work


def fetch_project_source_freshness_row(
    source_project_id: str,
    *,
    expected_group_id: str | None = None,
) -> Mapping[str, Any] | None:
    where = "WHERE p.id = %s"
    params: tuple[Any, ...] = (source_project_id,)
    if expected_group_id:
        where += " AND p.group_id = %s"
        params = (source_project_id, expected_group_id)

    with unit_of_work() as session:
        return session.fetchone(
            "SELECT p.id, p.name, p.updated_at AS project_updated_at, "
            "(SELECT MAX(r.created_at) FROM results r "
            " WHERE r.project_id = p.id) AS latest_result_at "
            f"FROM projects p {where}",
            params,
        )
