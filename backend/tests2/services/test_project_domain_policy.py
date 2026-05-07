from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.project_domain import build_project_id, resolve_project_group_id


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_build_project_id_preserves_legacy_readable_format() -> None:
    generated_at = datetime(2026, 5, 7, 8, 9, 10, tzinfo=timezone.utc)

    project_id = build_project_id(
        " Demo-Project 01 with a very long readable suffix ",
        generated_at,
        "abcdef123456",
    )

    assert project_id == "DemoProject_01_with_a_very_lon_20260507_080910_abcdef12"


def test_resolve_project_group_id_preserves_default_fallback_policy() -> None:
    assert resolve_project_group_id("grp-1", "default") == "grp-1"
    assert resolve_project_group_id(None, "default") == "default"
    assert resolve_project_group_id("", "default") == "default"
