"""Source-bound strangler — `app.db.config_db_service` facade.

Locks the config DB facade against new consumers. The facade
`app/db/config_db_service.py` is a compatibility re-export shell after
micro-batch G #9: every leaf cluster (users, projects, connections,
file_sources, datasources, api_connections, schedules, comparison_keys)
now lives under `app/db/config/<cluster>.py` with a 1:1 signature.

Five symbols are still owned by the facade and may legitimately be
imported through it:

  - `_safe_set_clause` / `_ALLOWED_UPDATE_FIELDS`: cross-cluster helpers
    consumed lazily by 5 leaf modules to break a facade ↔ leaf import
    cycle without duplicating the whitelist.
  - `init_db`: thin delegate over `pg_connection.init_db`.
  - `update_project_canvas_key_atomic`: 62-LOC SELECT FOR UPDATE +
    JSON merge transaction, kept verbatim to preserve atomic contract
    + the 8 dedicated test files patching `cfg_db.update_*` directly.

GROUPS cluster (`get_group`, `list_groups`, `create_group`,
`get_group_summary`, `delete_group`, `delete_group_cascade`) was
extracted to `app/db/config/groups.py` by
`BE-GO-READY-DB-ENGINE-002-GROUPS-CLUSTER-EXTRACTION`.
`BE-GO-READY-DB-ENGINE-003-GROUPS-CALLSITE-MIGRATION` then migrated
the production callsites: `groups_router.py` no longer imports the
facade (allowlist row removed), and `project_service.py` switched
its 2 group calls (`_ensure_default_group`) to the canonical leaf
while keeping the facade import for `update_project_canvas_key_atomic`
(SELECT FOR UPDATE + JSON merge stays in the facade).

Every other symbol re-exported by the facade has a canonical leaf
module. New code MUST import those names from the leaf, not from
the facade.

The allowlist below freezes the current set of production callers
that still resolve through the facade. Any new file under
`backend/app/` or `backend/workers/` that imports
`app.db.config_db_service` (in any form) breaks this test and must
either:

  1. Migrate to the canonical `app.db.config.<cluster>` module
     (preferred), or
  2. Be added to `_FACADE_PROD_ALLOWLIST` with a paired worker
     task explaining why migration was deferred.

Stays source-bound (AST walk, zero app imports) to mirror the
posture of `test_auth_facade_strangler_contract.py` and
`test_architecture_legacy_patterns.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP = _BACKEND_ROOT / "app"
_WORKERS = _BACKEND_ROOT / "workers"

_FACADE_DOTTED = "app.db.config_db_service"
_FACADE_PARENT = "app.db"
_FACADE_LEAF = "config_db_service"


# Files allowed to import `app.db.config_db_service` directly.
# Paths are POSIX-style relative to the backend root.
#
# Entries:
#   - `app/db/db_service.py`: cross-engine bulk re-export — KEEP_COMPAT.
#   - `app/db/config/{users,projects,schedules,connections,datasources}.py`:
#     lazy `_safe_set_clause` import — intentional cycle break.
#   - `app/core/auth/auth.py`: heavy `cfg_db.*` usage spanning users,
#     projects, groups; migration owned by future AUTH-ENGINE batch.
#   - `app/services/project_service.py`: removed by
#     BE-GO-READY-DB-ENGINE-007. The single remaining call
#     (`update_project_canvas_key_atomic`) was migrated to the
#     `app.db.config.projects` leaf (the function moved there
#     verbatim; the facade now re-exports it for back-compat
#     with the dedicated test patch points).
#   - Remaining entries: pending migration chunks listed in the death
#     map (BE-GO-READY-DB-ENGINE-001 report).
#
# Removed by BE-GO-READY-DB-ENGINE-003-GROUPS-CALLSITE-MIGRATION:
#   - `app/core/auth/groups_router.py` — now imports
#     `app.db.config.groups` directly; zero facade refs remain.
#
# Removed by BE-GO-READY-DB-ENGINE-004-FACADE-LOW-RISK-IMPORT-PURGE:
# the following files no longer import the facade after migrating
# their `cfg_db.get_project*` lookups to `app.db.config.projects`
# (no monkeypatch coupling on `cfg_db.<name>` for these consumers,
# so the migration is purely an import edit):
#   - `app/utils/result_store.py` — used `cfg_db.get_project_retention_n`.
#   - `app/utils/debug_payload_builder.py` — 2× `cfg_db.get_project`.
#   - `workers/tasks/pipeline_task.py` — `cfg_db.get_project`.
#   - `app/core/intermediate/db.py` — `cfg_db.get_project_retention_n`.
#   - `app/core/manipola/executor.py` — `cfg_db.get_project`.
#
# Removed by BE-GO-READY-DB-ENGINE-005-FACADE-LOW-RISK-PROD-PURGE
# (this batch): four further consumers migrated their lazy
# `_cfg_db.fetchall(...)` raw-SQL calls to the canonical
# `app.db.pg_connection.fetchall` (the same function the facade was
# re-exporting), and `api_connection_source_loading.py` migrated its
# `_cfg_db.get_project` lookup to the leaf `app.db.config.projects`:
#   - `app/core/arricchisci/api_connection_source_loading.py` — 1×
#     `_cfg_db.get_project` → `app.db.config.projects.get_project`.
#   - `app/core/arricchisci/api_connection_management.py` — 3×
#     `_cfg_db.fetchall` (api_connections) → `pg_connection.fetchall`.
#   - `app/core/datasources/service.py` — 2× `_cfg_db.fetchall`
#     (datasources) → `pg_connection.fetchall`.
#   - `app/services/connection_service.py` — 2× `_cfg_db.fetchall`
#     (connections) → `pg_connection.fetchall`.
# All four had ZERO test monkeypatch coupling on `cfg_db.<name>`, so
# the migration is purely an import edit + lazy-import rewire.
#
# Removed by BE-GO-READY-DB-ENGINE-006-FACADE-PROD-PURGE-NEXT
# (this batch):
#   - `app/core/auth/users_router.py` — superadmin admin-list path
#     migrated from `_cfg_db.fetchall("SELECT * FROM users ORDER BY
#     username")` to a new leaf helper
#     `app.db.config.users.list_all_users()` (returns the same row
#     shape; tested via `test_users_router_extraction_contract.py`
#     which only asserts handler location/URL routes -- no
#     monkeypatch coupling on `cfg_db.<name>` for the superadmin
#     branch). Eliminates the last raw-SQL-via-facade call from a
#     router and removes the `users_router` carve-out from the
#     architecture-guard `pg_connection`-direct-import allowlist
#     (the leaf owns the `pg_connection.fetchall` call now, so the
#     router stays compliant with `test_pg_connection_not_imported_from_new_routers`).
_FACADE_PROD_ALLOWLIST: frozenset[str] = frozenset({
    "app/db/db_service.py",
    "app/db/config/users.py",
    "app/db/config/projects.py",
    "app/db/config/schedules.py",
    "app/db/config/connections.py",
    "app/db/config/datasources.py",
    "app/core/auth/auth.py",
})


def _imports_facade(tree: ast.AST) -> bool:
    """True iff the AST contains an Import or ImportFrom node that
    references `app.db.config_db_service` in any of the three legal
    Python shapes:

        import app.db.config_db_service [as alias]
        from app.db.config_db_service import X [, Y, ...]
        from app.db import config_db_service [as alias]
    """
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


def test_no_new_facade_imports_outside_allowlist():
    """No production file may import `app.db.config_db_service` unless
    it is in `_FACADE_PROD_ALLOWLIST`.

    The allowlist freezes the death-map snapshot. Migrating a caller
    to the canonical leaf module trims the allowlist; adding a new
    caller requires explicit allowlist entry + paired migration plan.
    """
    violations: list[str] = []
    for root in _scan_roots():
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            if not _imports_facade(tree):
                continue
            rel = _rel(path)
            if rel not in _FACADE_PROD_ALLOWLIST:
                violations.append(rel)

    assert not violations, (
        "New imports of `app.db.config_db_service` detected outside "
        "the strangler allowlist. Migrate to the canonical "
        "`app.db.config.<cluster>` module, or add the file to "
        "`_FACADE_PROD_ALLOWLIST` with a paired migration task.\n"
        "Violations:\n  - " + "\n  - ".join(sorted(violations))
    )


def test_facade_allowlist_has_no_stale_entries():
    """Every entry in `_FACADE_PROD_ALLOWLIST` must currently import
    the facade. A stale entry means the migration already happened
    and the allowlist row should be deleted, tightening the strangler.
    """
    found: set[str] = set()
    for root in _scan_roots():
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            if _imports_facade(tree):
                found.add(_rel(path))

    stale = _FACADE_PROD_ALLOWLIST - found
    assert not stale, (
        "`_FACADE_PROD_ALLOWLIST` lists entries that no longer import "
        "`app.db.config_db_service`. Trim them to keep the strangler "
        "honest.\nStale entries:\n  - " + "\n  - ".join(sorted(stale))
    )
