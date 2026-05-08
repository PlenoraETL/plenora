"""
Contract test Bug #3 (2026-04-23): list_groups NULL-safe su description/name.

Blinda il contratto: GET /api/groups non deve andare in 500 quando un
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.shared.contracts.auth import CurrentUser
# BE-GO-READY-AUTH-ENGINE-021: `list_groups` no longer re-exported
# from `app.core.auth.auth`. Import from the canonical leaf
# `app.core.auth.groups_router` (handler home since AUTH-ENGINE-004
# extraction).
from app.core.auth.groups_router import list_groups
from app.core.auth.db import GroupRow


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def superadmin_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-sa",
        username="superadmin",
        group_id="sa-grp",
        role="superadmin",
    )


def test_list_groups_with_null_description_returns_empty_string(superadmin_user):
    """Bug #3: description=NULL nel DB → response con description=''."""
    groups_from_db = [
        GroupRow(id="g1", name="Gruppo A", description=None),
        GroupRow(id="g2", name="Gruppo B", description="desc valida"),
    ]

    with patch("app.core.auth.groups_router._groups.list_groups", return_value=groups_from_db):
        result = list_groups(current_user=superadmin_user)

    assert len(result) == 2
    assert result[0].description == ""  # era None, ora ""
    assert result[1].description == "desc valida"


def test_list_groups_with_null_name_returns_empty_string(superadmin_user):
    """Bug #3 guard simmetrico: name=NULL non deve crashare (robustezza)."""
    groups_from_db = [
        GroupRow(id="g1", name="", description="desc"),
    ]

    with patch("app.core.auth.groups_router._groups.list_groups", return_value=groups_from_db):
        result = list_groups(current_user=superadmin_user)

    assert len(result) == 1
    assert result[0].name == ""
    assert result[0].description == "desc"


def test_list_groups_missing_description_key_still_returns_empty(superadmin_user):
    """Bug #3 regression: la chiave NON presente continua a default a '' (caso pre-bug)."""
    groups_from_db = [
        GroupRow(id="g1", name="Gruppo A"),
    ]

    with patch("app.core.auth.groups_router._groups.list_groups", return_value=groups_from_db):
        result = list_groups(current_user=superadmin_user)

    assert len(result) == 1
    assert result[0].description == ""
