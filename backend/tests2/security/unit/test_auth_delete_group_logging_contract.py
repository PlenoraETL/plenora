"""
Contract test S5 — delete_group route non deve silenziare il cascade.

Scope S5 (inventory_report.md Sez. 5.1):
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# PR #34 P1-B: pattern B2.0 — handler stdlib agganciato al logger
# reale invece di monkeypatch del symbol module-level con MagicMock.
# `StructuredLogger` ha propagate=False, caplog non funziona out-of-
# the-box: usiamo handler custom (clone-da test_n4_formula_validator).
class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


# BE-GO-READY-AUTH-ENGINE-004: la logica di `delete_group` (incluso
# `logger.error("[CRASH] delete_group_cascade ...")`) e' stata
# spostata da `app/core/auth/auth.py` a `app/core/auth/groups_router.py`.
# Il record ERROR e' ora emesso dal logger del modulo nuovo.
_AUTH_LOGGER = "app.core.auth.groups_router"
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_GROUP_CLEANUP_PATH = (
    _BACKEND_ROOT / "app" / "core" / "auth" / "group_cleanup.py"
)


def _superadmin_user():
    return SimpleNamespace(
        user_id="root", username="root", group_id="", role="superadmin",
    )


def test_delete_group_cascade_delegates_intermediate_cleanup_boundary(monkeypatch):
    """Group cascade must not be orchestrated from auth.db."""
    from app.core.auth import group_cleanup

    class _Cursor:
        rowcount = 1

    intermediate_calls: list[str] = []

    def _execute(_sql, _params=()):
        return _Cursor()

    def _fetchall(sql, _params=()):
        if "SELECT id FROM projects" in sql:
            return [{"id": "project-1"}]
        return []

    monkeypatch.setattr(group_cleanup, "execute", _execute)
    monkeypatch.setattr(group_cleanup, "fetchall", _fetchall)
    monkeypatch.setattr(group_cleanup, "delete_results_for_project", lambda _pid: 2)
    monkeypatch.setattr(group_cleanup.auth_db, "delete_group", lambda _gid: True)
    monkeypatch.setattr(
        group_cleanup,
        "delete_intermediate_runs_for_group_project",
        lambda project_id: intermediate_calls.append(project_id) or 3,
    )

    report = group_cleanup.delete_group_cascade("group-1")

    assert intermediate_calls == ["project-1"]
    assert report["intermediate_runs"] == 3


def test_group_cleanup_imports_intermediate_cleanup_boundary_only():
    src = _GROUP_CLEANUP_PATH.read_text(encoding="utf-8")

    assert "app.core.intermediate.cleanup" in src
    assert "app.core.intermediate.db" not in src
    assert "delete_all_runs_for_canvas" not in src


def test_group_cleanup_execute_calls_do_not_use_f_string_sql():
    tree = ast.parse(_GROUP_CLEANUP_PATH.read_text(encoding="utf-8"))

    unsafe_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "execute":
            continue
        if node.args and isinstance(node.args[0], ast.JoinedStr):
            unsafe_calls.append(node.lineno)

    assert not unsafe_calls, (
        "group_cleanup must use static SQL constants for execute() calls; "
        f"f-string SQL found on lines {unsafe_calls}"
    )


def test_delete_group_cascade_failure_is_logged_then_raised_as_500(monkeypatch):
    """S5: quando delete_group_cascade solleva, il route handler DEVE
    loggare l'errore (con exc_info) e poi sollevare HTTPException 500.
    Il fallback pre-fix catturava `Exception as e` ma non usava mai `e`:
    il log diagnostico non veniva emesso → regressione di osservabilita'.
    """
    # BE-GO-READY-AUTH-ENGINE-021-USERS-GROUPS-REEXPORT-REMOVAL:
    # `delete_group` is no longer re-exported from `app.core.auth.auth`.
    # Call it via the canonical leaf module `app.core.auth.groups_router`
    # (where the handler actually lives since AUTH-ENGINE-004 extraction).
    #
    # BE-GO-READY-DB-ENGINE-003-GROUPS-CALLSITE-MIGRATION: groups_router
    # now binds the leaf as `groups_mod._groups`; patch that symbol
    # rather than the legacy `cfg_db` facade so the test exercises the
    # actual call path.
    from app.core.auth import groups_router as groups_mod

    monkeypatch.setattr(
        groups_mod._groups,
        "get_group",
        lambda gid: {"id": gid, "name": "g"},
    )

    boom = RuntimeError("simulated cascade failure")

    def _raising_cascade(gid):
        raise boom

    monkeypatch.setattr(
        groups_mod._group_cleanup,
        "delete_group_cascade",
        _raising_cascade,
    )

    target_logger = logging.getLogger(_AUTH_LOGGER)
    handler = _ListHandler()
    target_logger.addHandler(handler)
    try:
        with pytest.raises(HTTPException) as excinfo:
            groups_mod.delete_group("some_group", _superadmin_user())
    finally:
        target_logger.removeHandler(handler)

    assert excinfo.value.status_code == 500

    error_records = [r for r in handler.records if r.levelno == logging.ERROR]
    assert error_records, (
        "REGRESSIONE S5: delete_group_cascade ha sollevato ma nessun record "
        "ERROR e' stato emesso → silent swallow, diagnostica lato server persa."
    )

    # Almeno un record ERROR deve avere exc_info popolato (= logger.error fu
    # chiamato con exc_info=True, oppure dentro un except con exception()).
    error_with_exc = [r for r in error_records if r.exc_info is not None]
    assert error_with_exc, (
        "REGRESSIONE S5: logger.error chiamato senza exc_info, lo stack "
        "trace dell'eccezione originale non viene registrato."
    )

    # Il messaggio di log deve menzionare il group_id per correlazione
    assert any("some_group" in r.getMessage() for r in error_records), (
        "REGRESSIONE S5: il messaggio di log non include il group_id → "
        "difficile correlare il crash a una specifica operazione."
    )
