from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_results_project_fk_prevents_new_orphans_without_dropping_history() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "2026-07-16_z_add_results_project_fk.sql"
    ).read_text(encoding="utf-8")

    normalized = " ".join(migration.lower().split())
    assert "foreign key (project_id) references projects(id)" in normalized
    assert "on delete restrict" in normalized
    assert "not valid" in normalized
    assert "delete from results" not in normalized
