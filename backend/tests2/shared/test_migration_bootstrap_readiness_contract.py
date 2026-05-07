"""
Migration bootstrap/readiness contracts for 1.0 release visibility.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_migration_status_reports_empty_db_when_tracking_missing():
    from app.db import migrations_runner as mr

    with patch.object(
        mr,
        "list_migration_files",
        return_value=["2026-05-01_a.sql", "2026-05-02_b.sql"],
    ), patch.object(
        mr,
        "_read_applied_migrations",
        return_value=([], False),
    ):
        status = mr.get_migration_status()

    assert status.state == "empty"
    assert status.tracking_table_present is False
    assert status.pending_migrations == [
        "2026-05-01_a.sql",
        "2026-05-02_b.sql",
    ]
    assert status.aligned is False


def test_migration_status_reports_pending_when_tracking_exists():
    from app.db import migrations_runner as mr

    with patch.object(
        mr,
        "list_migration_files",
        return_value=["a.sql", "b.sql"],
    ), patch.object(
        mr,
        "_read_applied_migrations",
        return_value=(["a.sql"], True),
    ):
        status = mr.get_migration_status()

    assert status.state == "pending"
    assert status.pending_migrations == ["b.sql"]
    assert status.unknown_applied_migrations == []


def test_migration_status_reports_misaligned_unknown_applied_rows():
    from app.db import migrations_runner as mr

    with patch.object(
        mr,
        "list_migration_files",
        return_value=["a.sql"],
    ), patch.object(
        mr,
        "_read_applied_migrations",
        return_value=(["a.sql", "deleted.sql"], True),
    ):
        status = mr.get_migration_status()

    assert status.state == "misaligned"
    assert status.pending_migrations == []
    assert status.unknown_applied_migrations == ["deleted.sql"]


def test_migration_status_reports_migrated_when_files_match_tracking():
    from app.db import migrations_runner as mr

    with patch.object(
        mr,
        "list_migration_files",
        return_value=["a.sql", "b.sql"],
    ), patch.object(
        mr,
        "_read_applied_migrations",
        return_value=(["b.sql", "a.sql"], True),
    ):
        status = mr.get_migration_status()

    assert status.state == "migrated"
    assert status.aligned is True
    assert status.pending_migrations == []
    assert status.unknown_applied_migrations == []


def test_readiness_migrations_check_exposes_empty_db_state():
    from app.db.migrations_runner import MigrationStatus
    from app.observability import health as h

    status = MigrationStatus(
        state="empty",
        migration_files=["a.sql"],
        applied_migrations=[],
        pending_migrations=["a.sql"],
        unknown_applied_migrations=[],
        tracking_table_present=False,
    )

    with patch("app.db.migrations_runner.get_migration_status", return_value=status):
        ok, detail = h.check_migrations_aligned()

    assert ok is False
    assert "empty database" in detail
    assert "1 pending" in detail


def test_readiness_migrations_check_exposes_misaligned_state():
    from app.db.migrations_runner import MigrationStatus
    from app.observability import health as h

    status = MigrationStatus(
        state="misaligned",
        migration_files=["a.sql"],
        applied_migrations=["a.sql", "deleted.sql"],
        pending_migrations=[],
        unknown_applied_migrations=["deleted.sql"],
        tracking_table_present=True,
    )

    with patch("app.db.migrations_runner.get_migration_status", return_value=status):
        ok, detail = h.check_migrations_aligned()

    assert ok is False
    assert "misaligned" in detail
    assert "deleted.sql" in detail
