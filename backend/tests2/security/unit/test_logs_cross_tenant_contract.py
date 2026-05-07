"""
Contract test N6 — /api/logs fail-closed su project_id cross-tenant.

Scenario pre-fix:
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def _user(role="user", group_id="group_A"):
    return SimpleNamespace(
        user_id="u1", username="alice",
        group_id=group_id, role=role,
    )


@pytest.fixture(autouse=True)
def _stub_db(monkeypatch):
    """Stuba le query DB per isolare il test al solo auth path.
    Se il fix funziona, il DB non viene toccato su project cross-tenant."""
    from app.core.logs import router as logs_router
    fetchall_mock = MagicMock(return_value=[])
    fetchone_mock = MagicMock(return_value={"c": 0})
    monkeypatch.setattr(logs_router, "_fetchall", fetchall_mock)
    monkeypatch.setattr(logs_router, "_fetchone", fetchone_mock)
    return {"fetchall": fetchall_mock, "fetchone": fetchone_mock}


def test_get_logs_rejects_cross_tenant_project_id(monkeypatch, _stub_db):
    """Non-superadmin che filtra per project_id di un altro gruppo -> 403."""
    from app.core.logs import router as logs_router

    def fail_cross(pid, user):
        raise HTTPException(status_code=403, detail="Accesso negato")

    # Patch sul punto dove logs/router lo usa (not on authz_helpers module)
    monkeypatch.setattr(logs_router, "assert_project_visible", fail_cross)

    with pytest.raises(HTTPException) as exc:
        logs_router.get_logs(
            level=None, module=None, op=None,
            project_id="proj_di_altro_gruppo",
            search=None, after=None, before=None,
            current_user=_user(role="user", group_id="group_A"),
            limit=100, offset=0,
        )
    assert exc.value.status_code == 403
    # Criticamente: il DB NON deve essere stato interrogato
    assert _stub_db["fetchall"].call_count == 0
    assert _stub_db["fetchone"].call_count == 0


def test_get_logs_accepts_project_id_if_visible(monkeypatch, _stub_db):
    """Progetto visibile (owned o shared) -> query procede normale."""
    from app.core.logs import router as logs_router

    visible_mock = MagicMock(return_value={"id": "proj_ok", "group_id": "group_A"})
    monkeypatch.setattr(logs_router, "assert_project_visible", visible_mock)

    result = logs_router.get_logs(
        level=None, module=None, op=None,
        project_id="proj_ok",
        search=None, after=None, before=None,
        current_user=_user(role="user", group_id="group_A"),
        limit=100, offset=0,
    )
    assert result["total"] == 0
    assert visible_mock.call_count == 1
    assert _stub_db["fetchall"].call_count == 1
    assert _stub_db["fetchone"].call_count == 1


def test_get_logs_superadmin_bypasses_project_check(monkeypatch, _stub_db):
    """Superadmin puo' filtrare per qualsiasi project_id senza check."""
    from app.core.logs import router as logs_router

    visible_mock = MagicMock()
    monkeypatch.setattr(logs_router, "assert_project_visible", visible_mock)

    logs_router.get_logs(
        level=None, module=None, op=None,
        project_id="qualsiasi_project",
        search=None, after=None, before=None,
        current_user=_user(role="superadmin", group_id="admin"),
        limit=100, offset=0,
    )
    # Superadmin: assert_project_visible NON deve essere chiamato
    assert visible_mock.call_count == 0


def test_get_logs_no_project_id_skips_assertion(monkeypatch, _stub_db):
    """Senza project_id, assert_project_visible non viene chiamato
    (il filtro gruppo via SQL basta per la cross-tenant isolation)."""
    from app.core.logs import router as logs_router

    visible_mock = MagicMock()
    monkeypatch.setattr(logs_router, "assert_project_visible", visible_mock)

    logs_router.get_logs(
        level=None, module=None, op=None,
        project_id=None,
        search=None, after=None, before=None,
        current_user=_user(role="user", group_id="group_A"),
        limit=100, offset=0,
    )
    assert visible_mock.call_count == 0
