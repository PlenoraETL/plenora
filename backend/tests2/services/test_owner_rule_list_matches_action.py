"""La lista dice can_edit=true esattamente quando l'azione e' permessa.

Fase 5, correzione dopo la conferma T1+T3 (punto 8): per i file_source la
lista calcolava can_edit con compute_can_edit (solo admin, gerarchico)
mentre PUT/DELETE usano require_file_owner (admin o creatore). Regola unica:
``ownership.owns_or_admin`` decide sia l'azione (_check_ownership) sia il
flag delle liste che governano le stesse azioni.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.auth.ownership import _check_ownership, owns_or_admin
from app.shared.contracts.auth import CurrentUser


pytestmark = [pytest.mark.unit, pytest.mark.contract]

ROLES = ("esecutore", "viewer", "user", "admin", "superadmin")
CREATORS = ("me", "other", None)
GROUPS = ("g1", "g2")


def _allowed(resource: dict, user: CurrentUser) -> bool:
    try:
        _check_ownership(resource, "Risorsa", user)
    except HTTPException:
        return False
    return True


@pytest.mark.parametrize("role,creator,group", list(itertools.product(ROLES, CREATORS, GROUPS)))
def test_owns_or_admin_is_exactly_the_action_rule(role, creator, group):
    user = CurrentUser(user_id="me", username="me", group_id="g1", role=role)
    resource = {"id": "r1", "group_id": group, "created_by": creator}
    assert owns_or_admin(resource, user) is _allowed(resource, user)
    # Stessa risposta per gli oggetti (modelli Pydantic delle liste).
    assert owns_or_admin(SimpleNamespace(**resource), user) is _allowed(resource, user)


def test_file_list_and_detail_use_the_action_rule():
    router = (Path(__file__).resolve().parents[2] / "app/core/files/router.py").read_text(encoding="utf-8")
    assert "compute_can_edit" not in router
    assert router.count("can_edit = owns_or_admin(") == 2
    # Le azioni governate dal flag sono quelle di require_file_owner.
    assert router.count("Depends(require_file_owner)") == 2
