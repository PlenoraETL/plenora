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
#   - `app/core/auth/users_router.py`: USERS cluster + raw fetchall
#     for the admin list endpoint.
#   - `app/services/project_service.py`: only uses
#     `update_project_canvas_key_atomic` — kept verbatim in facade.
#     (groups callsites migrated by DB-ENGINE-003.)
#   - Remaining entries: pending migration chunks listed in the death
#     map (BE-GO-READY-DB-ENGINE-001 report).
#
# Removed by BE-GO-READY-DB-ENGINE-003-GROUPS-CALLSITE-MIGRATION:
#   - `app/core/auth/groups_router.py` — now imports
#     `app.db.config.groups` directly; zero facade refs remain.
_FACADE_PROD_ALLOWLIST: frozenset[str] = frozenset({
    "app/db/db_service.py",
    "app/db/config/users.py",
    "app/db/config/projects.py",
    "app/db/config/schedules.py",
    "app/db/config/connections.py",
    "app/db/config/datasources.py",
    "app/core/auth/auth.py",
    "app/core/auth/users_router.py",
    "app/core/datasources/service.py",
    "app/core/intermediate/db.py",
    "app/core/manipola/executor.py",
    "app/core/arricchisci/api_connection_source_loading.py",
    "app/core/arricchisci/api_connection_management.py",
    "app/services/connection_service.py",
    "app/services/project_service.py",
    "app/utils/result_store.py",
    "app/utils/debug_payload_builder.py",
    "workers/tasks/pipeline_task.py",
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
