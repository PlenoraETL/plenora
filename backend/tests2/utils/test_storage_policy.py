"""Hard invariants for the object-storage-only data plane."""

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_legacy_dispatch_modules_are_removed():
    root = Path(__file__).parents[2] / "app"
    assert not (root / "core" / "results" / "storage_policy.py").exists()
    assert not (root / "shared" / "resources" / "storage_dispatch.py").exists()


def test_writer_source_has_no_db_payload_branch():
    root = Path(__file__).parents[2] / "app"
    result_writer = (root / "core" / "results" / "blob_writer.py").read_text(
        encoding="utf-8"
    )
    file_writer = (root / "core" / "resources" / "blob_dispatch.py").read_text(
        encoding="utf-8"
    )
    assert "STORAGE_POLICY" not in result_writer
    assert "RESULT_DB_HARD_CAP_BYTES" not in result_writer
    assert '"data": db_data' not in file_writer
    assert "resolve_payload_target" not in file_writer


def test_final_schema_migration_drops_payload_columns_and_node_rows():
    migration = (
        Path(__file__).parents[2]
        / "migrations"
        / "2026-07-13_object_storage_only_schema.sql"
    ).read_text(encoding="utf-8")
    assert "ALTER TABLE results DROP COLUMN data" in migration
    assert "ALTER TABLE file_sources DROP COLUMN data" in migration
    assert "DROP TABLE node_rows" in migration
