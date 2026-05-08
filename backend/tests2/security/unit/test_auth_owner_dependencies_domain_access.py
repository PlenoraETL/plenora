"""Owner dependency domain access contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.shared.contracts.auth import CurrentUser


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def _user(role: str = "user") -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role=role,
    )


def test_require_schedule_owner_uses_scheduler_ownership_context(monkeypatch):
    import app.core.auth.owner_dependencies as owner_deps
    import app.core.scheduler.db as scheduler_db
    import app.core.scheduler.ownership as scheduler_ownership

    resource = {"id": "s-1", "group_id": "grp-1", "created_by": "u-1"}
    context = SimpleNamespace(ownership_resource=lambda: resource)
    checked: list[tuple[dict[str, object], str, CurrentUser]] = []

    monkeypatch.setattr(
        scheduler_db,
        "get_schedule",
        lambda _schedule_id: pytest.fail("auth must not call scheduler.db"),
    )
    monkeypatch.setattr(
        scheduler_ownership,
        "load_schedule_owner_context",
        lambda schedule_id: context if schedule_id == "s-1" else None,
    )
    monkeypatch.setattr(
        owner_deps,
        "_check_ownership",
        lambda res, label, user: checked.append((res, label, user)),
    )

    current_user = _user()

    assert owner_deps.require_schedule_owner("s-1", current_user) is current_user
    assert checked == [(resource, "Schedule", current_user)]


def test_require_file_owner_uses_files_ownership_context(monkeypatch):
    import app.core.auth.owner_dependencies as owner_deps
    import app.core.files.db as files_db
    import app.core.files.ownership as files_ownership

    resource = {"id": "f-1", "group_id": "grp-1", "created_by": "u-1"}
    context = SimpleNamespace(ownership_resource=lambda: resource)
    checked: list[tuple[dict[str, object], str, CurrentUser]] = []

    monkeypatch.setattr(
        files_db,
        "get_file_source",
        lambda _file_id: pytest.fail("auth must not call files.db"),
    )
    monkeypatch.setattr(
        files_ownership,
        "load_file_owner_context",
        lambda file_id: context if file_id == "f-1" else None,
    )
    monkeypatch.setattr(
        owner_deps,
        "_check_ownership",
        lambda res, label, user: checked.append((res, label, user)),
    )

    current_user = _user()

    assert owner_deps.require_file_owner("f-1", current_user) is current_user
    assert checked == [(resource, "File", current_user)]


def test_scheduler_owner_context_exposes_only_ownership_resource(monkeypatch):
    import app.core.scheduler.db as scheduler_db
    from app.core.scheduler.ownership import load_schedule_owner_context

    row = SimpleNamespace(id="s-1", group_id="grp-1", created_by="u-1")
    monkeypatch.setattr(scheduler_db, "get_schedule_row", lambda _schedule_id: row)

    context = load_schedule_owner_context("s-1")

    assert context is not None
    assert context.ownership_resource() == {
        "id": "s-1",
        "group_id": "grp-1",
        "created_by": "u-1",
    }


def test_file_owner_context_preserves_created_by(monkeypatch):
    import app.core.files.db as files_db
    from app.core.files.ownership import load_file_owner_context

    monkeypatch.setattr(
        files_db,
        "get_file_source",
        lambda _file_id: {
            "id": "f-1",
            "group_id": "grp-1",
            "created_by": "u-1",
            "name": "source.csv",
        },
    )

    context = load_file_owner_context("f-1")

    assert context is not None
    assert context.ownership_resource() == {
        "id": "f-1",
        "group_id": "grp-1",
        "created_by": "u-1",
    }
