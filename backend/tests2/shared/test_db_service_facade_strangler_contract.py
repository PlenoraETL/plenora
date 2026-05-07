"""
Source-bound strangler -- `app.db.db_service` facade.

BE-GO-READY-DB-ENGINE-011-DB-SERVICE-DEATHMAP-CONTRACT.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP = _BACKEND_ROOT / "app"
_WORKERS = _BACKEND_ROOT / "workers"

_FACADE_DOTTED = "app.db.db_service"
_FACADE_PARENT = "app.db"
_FACADE_LEAF = "db_service"
_FACADE_FILE = "app/db/db_service.py"


# Files allowed to import `app.db.db_service` directly. POSIX
# relative paths from the backend root.
#
# Snapshot taken 2026-05-06 by BE-GO-READY-DB-ENGINE-011 against
# the AST-level enumeration of every `Import` / `ImportFrom`
# referencing the facade in any of the three legal Python shapes:
#
#     import app.db.db_service [as alias]
#     from app.db.db_service import X [, Y, ...]
#     from app.db import db_service [as alias]
#
# The 38 entries split into the categories documented in the
# DB-ENGINE-011 audit (`.agent-temp/BE_DB_ENGINE_AUDIT.md`):
#
#   - Bootstrap-only consumer of `init_all_dbs` (1 file):
#       `app/bootstrap.py`. The function is the facade's own
#       orchestrator (not a re-export); migration target is a
#       dedicated `app.db.bootstrap_init` module if/when the
#       facade is finally retired.
#
#   - Heavy file-service consumers (1 file, ~8 lazy imports):
#       `app/services/file_service.py`. Mostly raw-SQL via
#       `db.fetchone`/`fetchall`/`execute` and result/log
#       persistence. Migration is the canonical "next big chunk"
#       for the BE-GO-READY-DB-ENGINE-012 sweep.
#
#   - Beat / worker tasks (3 files):
#       `workers/beat_scheduler.py` (7 lazy imports),
#       `workers/tasks/schedule_task.py`,
#       `workers/tasks/carica_task.py`.
#
#   - Auth surface (1 file):
#       `app/core/auth/shares_router.py`,
#     `app/core/auth/auth.py`, `app/core/auth/ownership.py`, and
#     `app/core/auth/users_router.py` were migrated by BE-ARCH-068-W5
#     to the narrow `app.db.config.users` / `app.db.config.groups`
#     leaves.
#     `app/core/auth/dependencies.py` was migrated by Worker 3 of
#     this batch (BE-GO-READY-AUTH-ENGINE-014 -- "Auth Dependencies
#     Off db_service"); the AST scan confirms zero remaining
#     facade imports there. Allowlist row NOT included.
#
#   - Test-monkeypatch surfaces (2 files; preserved per the
#     `noqa: F401  (test-monkeypatch surface preserved)` comment
#     attached to each `import`):
#       `app/core/arricchisci/api_connector.py`,
#       `app/core/arricchisci/canvas_orchestrator.py`.
#
#   - Cluster-CRUD callers via `db.get_*` / `db.list_*` /
#     `db.save_*` / `db.delete_*`:
#       `app/core/admin_system_status/service.py`,
#       `app/core/arricchisci/api_connection_management.py`,
#       `app/core/arricchisci/api_connection_rest_file.py`,
#       `app/core/arricchisci/execution_context.py`,
#       `app/core/artifacts/resolver.py`,
#       `app/core/carica/executor.py`,
#       `app/core/connections/router.py`,
#       `app/core/datasources/router.py`,
#       `app/core/datasources/service.py`,
#       `app/core/eseguibili/service.py`,
#       `app/core/features/features_service.py`,
#       `app/core/files/router.py`,
#       `app/core/imdf/router.py`,
#       `app/core/orchestrator/router.py`,
#       `app/core/scheduler/concurrency_manager.py`,
#       `app/core/scheduler/execution_manager.py`,
#       `app/core/scheduler/router.py`,
#       `app/core/scheduler/schedule_service.py`,
#       `app/core/tickets/router.py`,
#       `app/services/connection_service.py`,
#       `app/services/orphan_cleanup_service.py`,
#       `app/services/project_service.py`,
#       `app/services/schema_inference.py`,
#       `app/shared/authz_helpers.py`,
#       `app/utils/result_store.py`.
#
# Removed by BE-GO-READY-DB-ENGINE-011 (this batch):
#   - `app/core/eseguibili/db.py` migrated `import app.db.db_service
#     as db` to `from app.db import pg_connection as db`. Used
#     only `db.execute`/`fetchone`/`fetchall`, all of which are
#     pg_connection's canonical home (`db_service` re-exports
#     them transitively). Zero monkeypatch coupling on
#     `eseguibili.db.db.<name>`. Allowlist row removed.
_DB_SERVICE_PROD_ALLOWLIST: frozenset[str] = frozenset({
    # Bootstrap orchestrator — `init_all_dbs` lives on the facade.
    "app/bootstrap.py",
    # Arricchisci: test-monkeypatch surface (noqa: F401 preserved).
    "app/core/arricchisci/api_connection_management.py",
    "app/core/arricchisci/api_connection_rest_file.py",
    "app/core/arricchisci/api_connector.py",
    "app/core/arricchisci/canvas_orchestrator.py",
    # Auth sharing surface: generic shareable-resource lookup via raw SQL.
    "app/core/auth/shares_router.py",
    # Datasources service — many cluster-specific calls; deferred.
    "app/core/datasources/service.py",
    # Scheduler cluster — complex orchestration; deferred.
    "app/core/scheduler/concurrency_manager.py",
    "app/core/scheduler/execution_manager.py",
    "app/core/scheduler/router.py",
    "app/core/scheduler/schedule_service.py",
    # Services with many mixed usages; deferred.
    "app/services/connection_service.py",
    "app/services/file_service.py",
    "app/services/project_service.py",
    "app/utils/result_store.py",
    # Worker cluster — deferred.
    "workers/beat_scheduler.py",
    "workers/tasks/carica_task.py",
    "workers/tasks/schedule_task.py",
    #
    # Removed by BE-GO-READY-DB-ENGINE-013 (this batch):
    #   app/core/arricchisci/execution_context.py → app.db.config.projects.get_project
    #   app/core/carica/executor.py               → app.db.pg_connection.fetchone
    #   app/core/datasources/router.py            → app.db.pg_connection.fetchall
    #   app/core/features/features_service.py     → app.db.pg_connection (execute/fetchone/fetchall)
    #   app/core/files/router.py                  → app.db.config.file_sources.get_file_source
    #   app/core/imdf/router.py                   → app.db.config.projects.get_project
    #   app/core/orchestrator/router.py           → dead import deleted
    #   app/core/tickets/router.py                → app.db.config.file_sources.get_file_source
    #   app/services/orphan_cleanup_service.py    → app.db.pg_connection.fetchall
    #   app/shared/authz_helpers.py               → app.db.config.file_sources.get_file_source
})


def _imports_facade(tree: ast.AST) -> bool:
    """True iff the AST contains an Import / ImportFrom node that
    references `app.db.db_service` in any of the three legal
    Python shapes."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                if n.name == _FACADE_DOTTED:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == _FACADE_DOTTED:
                return True
            if node.module == _FACADE_PARENT:
                for n in node.names:
                    if n.name == _FACADE_LEAF:
                        return True
    return False


def _scan_roots() -> list[Path]:
    roots: list[Path] = []
    for r in (_APP, _WORKERS):
        if r.exists():
            roots.append(r)
    return roots


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _scan_importers() -> set[str]:
    """All production files that AST-import `app.db.db_service`,
    excluding the facade itself."""
    found: set[str] = set()
    for root in _scan_roots():
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            if not _imports_facade(tree):
                continue
            rel = _rel(path)
            if rel == _FACADE_FILE:
                # The facade file naturally references its own
                # name in its module body / docstring; AST scan
                # would catch any import statement, but it has
                # none. Defensive skip.
                continue
            found.add(rel)
    return found


def test_no_new_db_service_imports_outside_allowlist():
    """No production file may import `app.db.db_service` unless
    it is in `_DB_SERVICE_PROD_ALLOWLIST`.

    The allowlist freezes the death-map snapshot. Migrating a
    caller to the canonical leaf trims the allowlist; adding a
    new caller requires explicit allowlist entry + paired
    migration plan (see `BE_DB_ENGINE_AUDIT.md` DB-ENGINE-011
    "Top 5 next cuts" section).
    """
    found = _scan_importers()
    new = sorted(found - _DB_SERVICE_PROD_ALLOWLIST)
    assert not new, (
        "New imports of `app.db.db_service` detected outside the "
        "strangler allowlist. Migrate to the canonical "
        "`app.db.config.<cluster>` / `app.db.results_db_service` / "
        "`app.db.pg_connection` module, or add the file to "
        "`_DB_SERVICE_PROD_ALLOWLIST` with a paired migration "
        "task.\nNew importers:\n  - " + "\n  - ".join(new)
    )


def test_db_service_allowlist_has_no_stale_entries():
    """Every entry in `_DB_SERVICE_PROD_ALLOWLIST` must currently
    import the facade. A stale entry means the migration already
    happened and the allowlist row should be deleted, tightening
    the strangler.
    """
    found = _scan_importers()
    stale = sorted(_DB_SERVICE_PROD_ALLOWLIST - found)
    assert not stale, (
        "`_DB_SERVICE_PROD_ALLOWLIST` lists entries that no "
        "longer import `app.db.db_service`. Trim them to keep "
        "the strangler honest.\nStale entries:\n  - "
        + "\n  - ".join(stale)
    )


def test_db_service_facade_file_remains_a_thin_shim():
    """Sanity check: the facade file should stay a thin
    re-export shim. If `db_service.py` itself starts growing
    business logic, the strangler invariant ("burn down the
    facade") is at risk.

    The bar is intentionally loose: no ban on functions, just a
    LOC budget. The current facade is ~53 lines (10-line
    docstring + 30-symbol re-export from config_db_service +
    7-symbol re-export from results_db_service +
    `init_all_dbs`). 100 lines is a generous ceiling that flags
    a future "let me just add one more helper here" drift.
    """
    facade_path = _BACKEND_ROOT / _FACADE_FILE
    assert facade_path.is_file()
    line_count = sum(1 for _ in facade_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= 100, (
        f"`{_FACADE_FILE}` has grown to {line_count} lines (budget: "
        "100). The facade is meant to be a re-export shim and "
        "deathmap surface, not a place to hang new helpers. Move "
        "any new logic to a canonical leaf and keep the facade "
        "thin."
    )
