"""Store locale per lookup DB dei project_source in arricchisci."""

from __future__ import annotations

from app.db.pg_connection import unit_of_work
from app.core.arricchisci.types import JsonObject


def fetch_project_and_latest_result_timestamps(
    source_project_id: str,
) -> JsonObject | None:
    with unit_of_work() as session:
        return session.fetchone(
            "SELECT p.updated_at AS proj_ts, "
            "(SELECT MAX(r.created_at) FROM results r "
            "WHERE r.project_id = p.id) AS blob_ts "
            "FROM projects p WHERE p.id = %s",
            (source_project_id,),
        )
