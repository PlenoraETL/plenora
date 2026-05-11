from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.tickets import db as tdb


pytestmark = [pytest.mark.unit, pytest.mark.contract]


NOW = datetime(2026, 4, 19, tzinfo=timezone.utc)


def _ticket_row(**overrides) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "t1",
        "kind": "user_support",
        "title": "Ticket",
        "description": "",
        "severity": "medium",
        "status": "open",
        "category": "other",
        "created_by": "u1",
        "assignee": None,
        "group_id": "g1",
        "created_at": NOW,
        "updated_at": NOW,
        "resolved_at": None,
        "deleted_at": None,
    }
    row.update(overrides)
    return row


def test_get_ticket_missing_row_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(tdb, "fetchone", lambda *args, **kwargs: None)

    assert tdb.get_ticket("missing") is None


def test_ticket_row_rejects_null_required_field() -> None:
    with pytest.raises(ValueError, match="title"):
        tdb.ticket_row_from_db(_ticket_row(title=None))


def test_ticket_row_accepts_nullable_optional_fields() -> None:
    row = tdb.ticket_row_from_db(
        _ticket_row(assignee=None, resolved_at=None, deleted_at=None),
    )

    assert row.assignee is None
    assert row.resolved_at is None
    assert row.deleted_at is None


def test_ticket_row_rejects_string_timestamp() -> None:
    with pytest.raises(ValueError, match="created_at"):
        tdb.ticket_row_from_db(_ticket_row(created_at="2026-04-19T00:00:00Z"))


def test_comment_row_rejects_null_body() -> None:
    with pytest.raises(ValueError, match="body"):
        tdb.comment_row_from_db({
            "id": "c1",
            "ticket_id": "t1",
            "author_id": "u1",
            "body": None,
            "created_at": NOW,
            "author_username": None,
        })


def test_attachment_row_rejects_malformed_size() -> None:
    with pytest.raises(ValueError, match="size_bytes"):
        tdb.attachment_row_from_db({
            "id": "a1",
            "ticket_id": "t1",
            "file_source_id": "fs1",
            "uploaded_by": "u1",
            "uploaded_at": NOW,
            "file_name": "log.txt",
            "original_filename": "log.txt",
            "format": "txt",
            "size_bytes": "12",
            "uploaded_by_username": None,
        })
