"""Contract test BE-GO-READY-CONNECTION-ENGINE-003 — typed
project-id lookup helpers in `app.db.config.projects`.

Locks the boundary contract for:

  - `list_owned_project_ids(project_ids, *, owner_group_id) -> set[str]`
  - `list_project_ids_for_group(group_id) -> set[str]`

Both helpers were extracted from inline `pg_connection.fetchall`
calls in `projects/router.py` and `imdf/router.py` so those
routers can be removed from the architecture-guard allowlist.
The contract tests pin:

  1. Return type is ALWAYS `set[str]` (deduplicated, unordered).
  2. Empty / falsy inputs short-circuit WITHOUT touching the DB.
  3. Single-query batch SQL shape (no N+1) for the IN(...) helper.
  4. Parameter binding is positional (no SQL injection on inputs).
  5. Behavior is identical to the inline pre-extraction queries
     (regression for `projects/router._visible_project_ids_strict`
     and `imdf/router.list_bundles` ACL precheck).

The helpers use `app.db.pg_connection.fetchall`. The autouse
fixture `_stub_pg_connection_execute` in `tests2/conftest.py`
stubs `pg_connection.execute` to a no-op for unit tests; for
read paths we monkeypatch `pg_connection.fetchall` per-test so
we can spy on the SQL + params.
"""

from __future__ import annotations

from typing import List, Tuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_fetchall(monkeypatch, *, response):
    """Spy on the `fetchall` name imported into
    `app.db.config.projects`.

    The helpers under test do
    `from app.db.pg_connection import fetchall` at module-load time,
    so monkeypatching `pg_connection.fetchall` AFTER import is too
    late: the bound reference in `projects` already points at the
    real function. We patch the bound name in `projects` instead.

    Returns a list of `(sql, params)` tuples. The fake `fetchall`
    returns the supplied `response` (a list of dicts simulating the
    `RealDictCursor` shape used by the production helper)."""
    calls: List[Tuple[str, tuple]] = []

    def _fake_fetchall(sql, params=()):
        calls.append((sql, tuple(params)))
        return list(response)

    import app.db.config.projects as projects_dal

    monkeypatch.setattr(projects_dal, "fetchall", _fake_fetchall)
    return calls


# ---------------------------------------------------------------------------
# list_owned_project_ids
# ---------------------------------------------------------------------------


def test_list_owned_project_ids_short_circuits_on_empty_input(monkeypatch):
    from app.db.config.projects import list_owned_project_ids

    calls = _capture_fetchall(monkeypatch, response=[{"id": "x"}])
    assert list_owned_project_ids([], owner_group_id="g1") == set()
    assert list_owned_project_ids(None, owner_group_id="g1") == set()
    assert list_owned_project_ids({}, owner_group_id="g1") == set()
    # No DB hit on empty input.
    assert calls == []


def test_list_owned_project_ids_short_circuits_on_falsy_owner_group(monkeypatch):
    from app.db.config.projects import list_owned_project_ids

    calls = _capture_fetchall(monkeypatch, response=[{"id": "x"}])
    # The contract is fail-CLOSED on missing owner: caller (router)
    # must pre-decide superadmin / no-group cases. The helper itself
    # does not query the DB without an owner.
    assert list_owned_project_ids({"p1", "p2"}, owner_group_id="") == set()
    assert list_owned_project_ids({"p1", "p2"}, owner_group_id=None) == set()
    assert calls == []


def test_list_owned_project_ids_returns_set_from_fetchall(monkeypatch):
    from app.db.config.projects import list_owned_project_ids

    _capture_fetchall(
        monkeypatch,
        response=[{"id": "p1"}, {"id": "p3"}],  # p2 is filtered out by DB
    )
    out = list_owned_project_ids({"p1", "p2", "p3"}, owner_group_id="g1")
    assert isinstance(out, set)
    assert out == {"p1", "p3"}


def test_list_owned_project_ids_uses_single_batch_query(monkeypatch):
    from app.db.config.projects import list_owned_project_ids

    calls = _capture_fetchall(monkeypatch, response=[])
    list_owned_project_ids({"p1", "p2", "p3"}, owner_group_id="g1")
    # Exactly ONE query (batch IN(...)), not N lookups.
    assert len(calls) == 1
    sql, params = calls[0]
    # Single-statement SELECT with `IN (?,?,?)` and AND group_id = ?.
    assert "SELECT id FROM projects" in sql
    assert "IN (" in sql and ")" in sql
    assert "group_id = ?" in sql
    # 3 placeholders for ids + 1 for owner_group_id = 4 params.
    assert sql.count("?") == 4
    # Last param is always the owner_group_id; the leading 3 are the
    # project ids in arbitrary order (set iteration). Cannot assume
    # order, only the count + presence.
    assert params[-1] == "g1"
    assert set(params[:-1]) == {"p1", "p2", "p3"}


def test_list_owned_project_ids_dedupes_input(monkeypatch):
    from app.db.config.projects import list_owned_project_ids

    calls = _capture_fetchall(monkeypatch, response=[{"id": "p1"}])
    # Iterable with duplicates and a falsy entry should be deduped
    # before the SQL is built.
    out = list_owned_project_ids(
        ["p1", "p1", "", "p1", None], owner_group_id="g1",
    )
    assert out == {"p1"}
    sql, params = calls[0]
    assert sql.count("?") == 2  # 1 id + 1 group_id
    assert params == ("p1", "g1")


# ---------------------------------------------------------------------------
# list_project_ids_for_group
# ---------------------------------------------------------------------------


def test_list_project_ids_for_group_short_circuits_on_falsy_group(monkeypatch):
    from app.db.config.projects import list_project_ids_for_group

    calls = _capture_fetchall(monkeypatch, response=[{"id": "p1"}])
    assert list_project_ids_for_group("") == set()
    assert list_project_ids_for_group(None) == set()
    # No DB hit when the group is missing.
    assert calls == []


def test_list_project_ids_for_group_returns_set(monkeypatch):
    from app.db.config.projects import list_project_ids_for_group

    _capture_fetchall(
        monkeypatch,
        response=[{"id": "p1"}, {"id": "p2"}, {"id": "p3"}],
    )
    out = list_project_ids_for_group("g1")
    assert isinstance(out, set)
    assert out == {"p1", "p2", "p3"}


def test_list_project_ids_for_group_uses_single_query(monkeypatch):
    from app.db.config.projects import list_project_ids_for_group

    calls = _capture_fetchall(monkeypatch, response=[])
    list_project_ids_for_group("g1")
    assert len(calls) == 1
    sql, params = calls[0]
    assert sql == "SELECT id FROM projects WHERE group_id = ?"
    assert params == ("g1",)


def test_list_project_ids_for_group_returns_empty_set_for_unknown_group(monkeypatch):
    from app.db.config.projects import list_project_ids_for_group

    _capture_fetchall(monkeypatch, response=[])
    assert list_project_ids_for_group("g-nobody") == set()


# ---------------------------------------------------------------------------
# Router-level integration: imports use the new helpers, not pg_connection
# ---------------------------------------------------------------------------


def test_projects_router_uses_helper_not_pg_connection():
    """`projects/router._visible_project_ids_strict` must delegate to
    `list_owned_project_ids` and not import `pg_connection` directly."""
    from app.core.projects import router as projects_router

    src = open(projects_router.__file__, encoding="utf-8").read()
    # Hard guard: no pg_connection import in projects/router.py.
    assert "from app.db.pg_connection" not in src
    assert "import app.db.pg_connection" not in src
    # And the helper is referenced.
    assert "list_owned_project_ids" in src


def test_imdf_router_uses_helper_not_pg_connection():
    """`imdf/router.list_bundles` must delegate to
    `list_project_ids_for_group` and not import `pg_connection`
    directly."""
    from app.core.imdf import router as imdf_router

    src = open(imdf_router.__file__, encoding="utf-8").read()
    assert "from app.db.pg_connection" not in src
    assert "import app.db.pg_connection" not in src
    assert "list_project_ids_for_group" in src
