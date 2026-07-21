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
_CLEANUP_REGISTRY_PATH = (
    _BACKEND_ROOT / "app" / "core" / "auth" / "cleanup_registry.py"
)
# Epic E: il meccanismo del registry vive nel lifecycle neutro, la
# composizione dei default al composition root. `cleanup_registry` in
# auth resta come wrapper compatibile.
_LIFECYCLE_REGISTRY_PATH = (
    _BACKEND_ROOT / "app" / "core" / "lifecycle" / "group_cleanup.py"
)
_CLEANUP_WIRING_PATH = (
    _BACKEND_ROOT / "app" / "runtime" / "group_cleanup_wiring.py"
)


def _superadmin_user():
    return SimpleNamespace(
        user_id="root", username="root", group_id="", role="superadmin",
    )


def test_delete_group_cascade_delegates_intermediate_cleanup_boundary(monkeypatch):
    """Project cascade cleanup must use the intermediate cleanup boundary."""
    from app.core.projects import cleanup as project_cleanup

    intermediate_calls: list[str] = []

    monkeypatch.setattr(
        project_cleanup.project_db,
        "list_project_ids_for_group",
        lambda _gid: {"project-1"},
    )
    monkeypatch.setattr(project_cleanup, "delete_results_for_project", lambda _pid: 2)
    monkeypatch.setattr(
        project_cleanup,
        "delete_intermediate_runs_for_group_project",
        lambda project_id: intermediate_calls.append(project_id) or 3,
    )
    monkeypatch.setattr(
        project_cleanup,
        "delete_comparison_keys_for_project",
        lambda _pid: None,
    )
    monkeypatch.setattr(
        project_cleanup.project_db,
        "delete_project",
        lambda _pid, group_id=None: True,
    )

    report = project_cleanup.delete_projects_for_group(
        "group-1",
        SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    )

    assert intermediate_calls == ["project-1"]
    assert report.intermediate_runs == 3


def test_delete_group_cascade_delegates_registered_cleanup_handlers(monkeypatch):
    from app.core.auth import cleanup_registry, group_cleanup

    calls: list[tuple[str, str]] = []

    def _handler(group_id, report, _log):
        calls.append(("handler", group_id))
        report["schedules"] = 7

    monkeypatch.setattr(
        cleanup_registry,
        "GROUP_CLEANUP_HANDLERS",
        (
            cleanup_registry.GroupCleanupHandler(
                name="test_handler",
                report_keys=("schedules",),
                cleanup=_handler,
            ),
        ),
    )
    monkeypatch.setattr(group_cleanup.auth_db, "delete_group", lambda _gid: True)

    report = group_cleanup.delete_group_cascade("group-1")

    assert calls == [("handler", "group-1")]
    assert report["schedules"] == 7


def test_group_cleanup_handlers_are_isolated_and_logged_in_order():
    from app.core.auth import cleanup_registry

    calls: list[str] = []
    records: list[tuple[str, str, tuple[object, ...]]] = []

    class _Log:
        def info(self, msg, *args, **_kwargs):
            records.append(("info", msg, args))

        def warning(self, msg, *args, **_kwargs):
            records.append(("warning", msg, args))

    def _ok(name):
        def _handler(_group_id, report, _log):
            calls.append(name)
            report[name] = len(calls)

        return _handler

    def _boom(_group_id, _report, _log):
        calls.append("middle")
        raise RuntimeError("middle failed")

    handlers = (
        cleanup_registry.GroupCleanupHandler(
            name="first",
            report_keys=("first",),
            cleanup=_ok("first"),
        ),
        cleanup_registry.GroupCleanupHandler(
            name="middle",
            report_keys=("middle",),
            cleanup=_boom,
        ),
        cleanup_registry.GroupCleanupHandler(
            name="last",
            report_keys=("last",),
            cleanup=_ok("last"),
        ),
    )
    report = {"first": 0, "middle": 0, "last": 0}

    cleanup_registry.run_group_cleanup_handlers(
        "group-1",
        report,
        _Log(),
        handlers=handlers,
    )

    assert calls == ["first", "middle", "last"]
    assert report == {"first": 1, "middle": 0, "last": 3}

    warning_records = [record for record in records if record[0] == "warning"]
    assert len(warning_records) == 1
    assert "failed after %.2fms" in warning_records[0][1]
    assert warning_records[0][2][0] == "middle"
    assert isinstance(warning_records[0][2][1], float)
    assert warning_records[0][2][2:4] == (2, 3)

    info_records = [record for record in records if record[0] == "info"]
    assert all("completed in %.2fms" in record[1] for record in info_records)
    assert all(isinstance(record[2][1], float) for record in info_records)
    completed_names = [record[2][0] for record in info_records]
    assert completed_names == ["first", "last"]


def test_delete_group_cascade_keeps_group_when_domain_cleanup_fails(monkeypatch):
    from app.core.auth import cleanup_registry, group_cleanup

    auth_deletes: list[str] = []

    def _boom(_group_id, _report, _log):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(
        cleanup_registry,
        "GROUP_CLEANUP_HANDLERS",
        (
            cleanup_registry.GroupCleanupHandler(
                name="delete_files",
                report_keys=("file_sources",),
                cleanup=_boom,
            ),
        ),
    )
    monkeypatch.setattr(
        group_cleanup.auth_db,
        "delete_group",
        lambda group_id: auth_deletes.append(group_id),
    )

    with pytest.raises(group_cleanup.GroupCleanupIncompleteError):
        group_cleanup.delete_group_cascade("group-1")

    assert auth_deletes == []


def test_delete_group_cascade_runs_handlers_before_final_auth_delete_failure(monkeypatch):
    from app.core.auth import cleanup_registry, group_cleanup

    calls: list[str] = []

    def _handler(name):
        def _cleanup(_group_id, _report, _log):
            calls.append(name)

        return _cleanup

    monkeypatch.setattr(
        cleanup_registry,
        "GROUP_CLEANUP_HANDLERS",
        (
            cleanup_registry.GroupCleanupHandler(
                name="first",
                report_keys=(),
                cleanup=_handler("first"),
            ),
            cleanup_registry.GroupCleanupHandler(
                name="second",
                report_keys=(),
                cleanup=_handler("second"),
            ),
        ),
    )

    def _delete_group(_group_id):
        calls.append("auth_delete")
        raise RuntimeError("final delete failed")

    monkeypatch.setattr(group_cleanup.auth_db, "delete_group", _delete_group)

    with pytest.raises(RuntimeError, match="final delete failed"):
        group_cleanup.delete_group_cascade("group-1")

    assert calls == ["first", "second", "auth_delete"]


def test_group_cleanup_imports_intermediate_cleanup_boundary_only():
    group_cleanup_src = _GROUP_CLEANUP_PATH.read_text(encoding="utf-8")
    registry_src = _CLEANUP_REGISTRY_PATH.read_text(encoding="utf-8")
    wiring_src = _CLEANUP_WIRING_PATH.read_text(encoding="utf-8")

    assert "app.core.intermediate.cleanup" not in group_cleanup_src
    assert "app.core.intermediate.cleanup" not in registry_src
    assert "app.core.intermediate.cleanup" not in wiring_src
    assert "app.core.projects.cleanup" in registry_src
    assert "app.core.results.cleanup" not in registry_src
    assert "app.core.results.cleanup" not in wiring_src
    assert "app.core.intermediate.db" not in registry_src
    assert "app.core.intermediate.db" not in wiring_src
    assert "delete_all_runs_for_canvas" not in registry_src
    assert "delete_all_runs_for_canvas" not in wiring_src


def test_group_cleanup_registry_composes_domain_owned_handlers():
    """Epic E: la composizione domain-owned vive nel wiring composition
    root, non in auth. Il wrapper auth non deve importare i cleanup
    verticali (guardia E5 in test_group_cleanup_policy_contract)."""
    wiring_src = _CLEANUP_WIRING_PATH.read_text(encoding="utf-8")
    lifecycle_src = _LIFECYCLE_REGISTRY_PATH.read_text(encoding="utf-8")

    assert "app.core.scheduler.cleanup" in wiring_src
    assert "app.core.arricchisci.cleanup" in wiring_src
    assert "app.core.datasources.cleanup" in wiring_src
    assert "app.core.connections.cleanup" in wiring_src
    assert "app.core.files.cleanup" in wiring_src
    assert "app.core.auth.user_cleanup" in wiring_src
    assert "delete_file_sources_for_group" in wiring_src
    assert "delete_users_for_group" in wiring_src
    assert "delete_log_runs_for_group" in wiring_src
    for src in (wiring_src, lifecycle_src):
        assert "app.db.pg_connection" not in src
        assert "_delete_group_scoped" not in src
        assert "DELETE FROM schedules WHERE group_id" not in src
        assert "DELETE FROM api_connections WHERE group_id" not in src
        assert "DELETE FROM datasources WHERE group_id" not in src
        assert "DELETE FROM connections WHERE group_id" not in src
        assert "DELETE FROM file_sources WHERE group_id" not in src
        assert "DELETE FROM users WHERE group_id" not in src
        assert "DELETE FROM log_runs WHERE group_id" not in src


def test_group_cleanup_registry_does_not_unpack_project_cleanup_report():
    wiring_src = _CLEANUP_WIRING_PATH.read_text(encoding="utf-8")

    assert "_cleanup_projects" not in wiring_src
    assert "project_report.projects" not in wiring_src
    assert "project_report.results" not in wiring_src
    assert "project_report.intermediate_runs" not in wiring_src
    assert "GROUP_PROJECT_CLEANUP_REPORT_KEYS" in wiring_src
    assert "delete_projects_for_group_report" in wiring_src


def test_group_cleanup_registry_execute_calls_do_not_use_dynamic_sql():
    sources = {
        "group_cleanup.py": _GROUP_CLEANUP_PATH.read_text(encoding="utf-8"),
        "cleanup_registry.py": _CLEANUP_REGISTRY_PATH.read_text(encoding="utf-8"),
        "lifecycle/group_cleanup.py": _LIFECYCLE_REGISTRY_PATH.read_text(encoding="utf-8"),
        "group_cleanup_wiring.py": _CLEANUP_WIRING_PATH.read_text(encoding="utf-8"),
    }

    unsafe_calls = []
    for filename, src in sources.items():
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "execute":
                continue
            if not node.args:
                continue
            sql_arg = node.args[0]
            if isinstance(sql_arg, ast.JoinedStr | ast.BinOp):
                unsafe_calls.append(f"{filename}:{node.lineno}")
            if isinstance(sql_arg, ast.Call):
                unsafe_calls.append(f"{filename}:{node.lineno}")

    assert not unsafe_calls, (
        "group cleanup must use static SQL constants for execute() calls; "
        f"dynamic SQL found at {unsafe_calls}"
    )


def test_delete_group_cascade_failure_is_logged_then_raised_as_500(monkeypatch):
    """S5: quando delete_group_cascade solleva, il route handler DEVE
    loggare l'errore (con exc_info) e poi sollevare HTTPException 500.
    Il fallback Previous behavior catturava `Exception as e` ma non usava mai `e`:
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
