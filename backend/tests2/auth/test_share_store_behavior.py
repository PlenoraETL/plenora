"""Behavioral unit tests for app.core.auth.share_store.

The module is a thin PostgreSQL adapter: every public function builds SQL and
delegates to ``app.db.pg_connection`` helpers (``fetchone``, ``get_connection``,
``put_connection``). We do not touch a real DB here. Instead we patch those
helpers (which are imported into the module namespace as ``_pg_fetchone``,
``_pg_get_conn``, ``_pg_put_conn``) with light fakes that record the SQL/params
they receive and return canned rows. This lets us assert on:

  * the SQL that gets built (table/fk interpolation, the right WHERE clauses),
  * the params passed (positional binding order),
  * the shape of the return value (set / list / bool / dict / None),
  * branch coverage (superadmin vs not, owner-shortcut, empty results,
    rowcount-based booleans, rollback on error, connection always returned).

Rows are modeled as plain dicts because pg_connection uses dict-row cursors.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest

from app.core.auth import share_store

pytestmark = [pytest.mark.unit, pytest.mark.security]


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeCursor:
    """Minimal psycopg-like cursor.

    ``rows`` is a list of result sets (one per ``execute`` call) used to feed
    ``fetchall``/``fetchone``. ``rowcount`` is returned after the matching
    execute. Records every ``(sql, params)`` in ``calls``.
    """

    def __init__(self, result_sets: list[list[dict]] | None = None,
                 rowcounts: list[int] | None = None) -> None:
        self._result_sets = list(result_sets or [])
        self._rowcounts = list(rowcounts or [])
        self.calls: list[tuple[str, tuple]] = []
        self._last: list[dict] = []
        self.rowcount = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.calls.append((sql, params))
        self._last = self._result_sets.pop(0) if self._result_sets else []
        self.rowcount = self._rowcounts.pop(0) if self._rowcounts else 0

    def fetchall(self) -> list[dict]:
        return list(self._last)

    def fetchone(self) -> dict | None:
        return self._last[0] if self._last else None


class FakeConn:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.rollback_raises = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        if self.rollback_raises:
            raise RuntimeError("rollback failed")
        self.rolled_back = True


@contextmanager
def _patched(cursor: FakeCursor, *, fetchone_row: dict | None = None):
    """Patch the three pg helpers used by share_store.

    Yields (conn, put_calls) so tests can assert commit/rollback/return.
    """
    conn = FakeConn(cursor)
    put_calls: list[Any] = []

    def _put(c: Any) -> None:
        put_calls.append(c)

    with patch.object(share_store, "_pg_get_conn", return_value=conn), \
            patch.object(share_store, "_pg_put_conn", side_effect=_put), \
            patch.object(share_store, "_pg_fetchone", return_value=fetchone_row):
        yield conn, put_calls


# --------------------------------------------------------------------------- #
# fetch_shareable_resource_owner
# --------------------------------------------------------------------------- #
def test_fetch_owner_returns_dict_when_row_present() -> None:
    row = {"id": "r1", "group_id": "g7"}
    with patch.object(share_store, "_pg_fetchone", return_value=row) as m:
        out = share_store.fetch_shareable_resource_owner("layers", "r1")
    assert out == {"id": "r1", "group_id": "g7"}
    sql, params = m.call_args.args
    assert "FROM layers" in sql
    assert "WHERE id = %s" in sql
    assert params == ("r1",)


def test_fetch_owner_returns_none_when_no_row() -> None:
    with patch.object(share_store, "_pg_fetchone", return_value=None):
        out = share_store.fetch_shareable_resource_owner("layers", "missing")
    assert out is None


def test_fetch_owner_copies_row_into_plain_dict() -> None:
    # The function does dict(row); ensure mutating result doesn't affect source.
    src = {"id": "r1", "group_id": None}
    with patch.object(share_store, "_pg_fetchone", return_value=src):
        out = share_store.fetch_shareable_resource_owner("layers", "r1")
    out["group_id"] = "changed"
    assert src["group_id"] is None
    assert out["group_id"] == "changed"


# --------------------------------------------------------------------------- #
# fetch_visible_resource_ids
# --------------------------------------------------------------------------- #
def test_visible_ids_superadmin_selects_all() -> None:
    cur = FakeCursor(result_sets=[[{"id": "a"}, {"id": "b"}, {"id": "c"}]])
    with _patched(cur) as (conn, put_calls):
        out = share_store.fetch_visible_resource_ids(
            "layers", "layer_shares", "layer_id",
            user_group_id="g1", magic_all="ALL", is_superadmin=True,
        )
    assert out == {"a", "b", "c"}
    # Superadmin path runs exactly one query against the resource table only.
    assert len(cur.calls) == 1
    sql, params = cur.calls[0]
    assert "SELECT id FROM layers" in sql
    assert "UNION" not in sql
    assert params == () or params is None or params == tuple()
    assert put_calls == [conn]  # connection always returned


def test_visible_ids_non_superadmin_union_and_params() -> None:
    cur = FakeCursor(result_sets=[[{"id": "own1"}, {"id": "shared1"}]])
    with _patched(cur) as (conn, put_calls):
        out = share_store.fetch_visible_resource_ids(
            "layers", "layer_shares", "layer_id",
            user_group_id="g1", magic_all="ALL",
        )
    assert out == {"own1", "shared1"}
    sql, params = cur.calls[0]
    assert "UNION" in sql
    assert "WHERE group_id = %s" in sql
    assert "layer_shares" in sql
    # Param order: user_group_id (own), then user_group_id + magic_all (shares).
    assert params == ("g1", "g1", "ALL")
    assert put_calls == [conn]


def test_visible_ids_empty_result_returns_empty_set() -> None:
    cur = FakeCursor(result_sets=[[]])
    with _patched(cur):
        out = share_store.fetch_visible_resource_ids(
            "layers", "layer_shares", "layer_id", "g1", "ALL",
        )
    assert out == set()


def test_visible_ids_rolls_back_and_reraises_then_returns_conn() -> None:
    class BoomCursor(FakeCursor):
        def execute(self, sql: str, params: tuple = ()) -> None:
            raise RuntimeError("db boom")

    cur = BoomCursor()
    with _patched(cur) as (conn, put_calls):
        with pytest.raises(RuntimeError, match="db boom"):
            share_store.fetch_visible_resource_ids(
                "layers", "layer_shares", "layer_id", "g1", "ALL",
            )
    assert conn.rolled_back is True
    assert put_calls == [conn]  # finally still returns the connection


def test_visible_ids_swallows_rollback_failure_but_reraises_original() -> None:
    class BoomCursor(FakeCursor):
        def execute(self, sql: str, params: tuple = ()) -> None:
            raise ValueError("original error")

    cur = BoomCursor()
    with _patched(cur) as (conn, put_calls):
        conn.rollback_raises = True
        with pytest.raises(ValueError, match="original error"):
            share_store.fetch_visible_resource_ids(
                "layers", "layer_shares", "layer_id", "g1", "ALL",
            )
    # rollback raised internally but was swallowed; original error propagates.
    assert put_calls == [conn]


# --------------------------------------------------------------------------- #
# check_resource_visible
# --------------------------------------------------------------------------- #
def test_check_visible_owner_match_shortcuts_true_without_share_query() -> None:
    # owner group passed in == user group -> immediate True, no share lookup.
    cur = FakeCursor()
    with _patched(cur) as (conn, put_calls):
        out = share_store.check_resource_visible(
            "layers", "layer_shares", "layer_id",
            resource_id="r1", user_group_id="g1", magic_all="ALL",
            resource_owner_group_id="g1",
        )
    assert out is True
    assert cur.calls == []  # no query at all
    assert put_calls == [conn]


def test_check_visible_resolves_owner_when_not_provided_and_matches() -> None:
    # First execute resolves owner group_id == user group -> True.
    cur = FakeCursor(result_sets=[[{"group_id": "g1"}]])
    with _patched(cur) as (conn, _):
        out = share_store.check_resource_visible(
            "layers", "layer_shares", "layer_id",
            resource_id="r1", user_group_id="g1", magic_all="ALL",
        )
    assert out is True
    assert len(cur.calls) == 1  # only the owner-resolution query
    sql, params = cur.calls[0]
    assert "SELECT group_id FROM layers" in sql
    assert params == ("r1",)


def test_check_visible_unknown_resource_returns_false() -> None:
    cur = FakeCursor(result_sets=[[]])  # owner query finds nothing
    with _patched(cur) as (conn, put_calls):
        out = share_store.check_resource_visible(
            "layers", "layer_shares", "layer_id",
            resource_id="ghost", user_group_id="g1", magic_all="ALL",
        )
    assert out is False
    assert len(cur.calls) == 1
    assert put_calls == [conn]


def test_check_visible_shared_row_found_returns_true() -> None:
    # owner provided differs -> share lookup; share table returns a row.
    cur = FakeCursor(result_sets=[[{"?column?": 1}]])
    with _patched(cur) as (conn, _):
        out = share_store.check_resource_visible(
            "layers", "layer_shares", "layer_id",
            resource_id="r1", user_group_id="g2", magic_all="ALL",
            resource_owner_group_id="g1",
        )
    assert out is True
    sql, params = cur.calls[0]
    assert "FROM layer_shares" in sql
    assert "layer_id = %s" in sql
    assert params == ("r1", "g2", "ALL")


def test_check_visible_no_share_row_returns_false() -> None:
    cur = FakeCursor(result_sets=[[]])  # share lookup empty
    with _patched(cur) as (conn, put_calls):
        out = share_store.check_resource_visible(
            "layers", "layer_shares", "layer_id",
            resource_id="r1", user_group_id="g2", magic_all="ALL",
            resource_owner_group_id="g1",
        )
    assert out is False
    assert put_calls == [conn]


def test_check_visible_resolved_owner_differs_then_share_lookup() -> None:
    # Owner resolved (g9) != user (g2); falls through to share lookup (found).
    cur = FakeCursor(result_sets=[[{"group_id": "g9"}], [{"x": 1}]])
    with _patched(cur) as (conn, _):
        out = share_store.check_resource_visible(
            "layers", "layer_shares", "layer_id",
            resource_id="r1", user_group_id="g2", magic_all="ALL",
        )
    assert out is True
    assert len(cur.calls) == 2  # owner resolution + share lookup


# --------------------------------------------------------------------------- #
# list_resource_share_group_ids
# --------------------------------------------------------------------------- #
def test_list_share_group_ids_returns_ordered_list() -> None:
    cur = FakeCursor(result_sets=[[{"group_id": "a"}, {"group_id": "b"}]])
    with _patched(cur) as (conn, put_calls):
        out = share_store.list_resource_share_group_ids(
            "layer_shares", "layer_id", "r1",
        )
    assert out == ["a", "b"]
    sql, params = cur.calls[0]
    assert "FROM layer_shares" in sql
    assert "ORDER BY group_id" in sql
    assert params == ("r1",)
    assert put_calls == [conn]


def test_list_share_group_ids_empty() -> None:
    cur = FakeCursor(result_sets=[[]])
    with _patched(cur):
        out = share_store.list_resource_share_group_ids(
            "layer_shares", "layer_id", "r1",
        )
    assert out == []


# --------------------------------------------------------------------------- #
# insert_resource_share
# --------------------------------------------------------------------------- #
def test_insert_share_returns_true_when_row_affected() -> None:
    cur = FakeCursor(rowcounts=[1])
    with _patched(cur) as (conn, put_calls):
        out = share_store.insert_resource_share(
            "layer_shares", "layer_id", "r1", "g1",
        )
    assert out is True
    sql, params = cur.calls[0]
    assert "INSERT INTO layer_shares" in sql
    assert "ON CONFLICT DO NOTHING" in sql
    assert params == ("r1", "g1")
    assert conn.committed is True
    assert put_calls == [conn]


def test_insert_share_returns_false_when_conflict_no_op() -> None:
    cur = FakeCursor(rowcounts=[0])  # ON CONFLICT DO NOTHING -> 0 rows
    with _patched(cur) as (conn, _):
        out = share_store.insert_resource_share(
            "layer_shares", "layer_id", "r1", "g1",
        )
    assert out is False
    assert conn.committed is True


# --------------------------------------------------------------------------- #
# delete_resource_share
# --------------------------------------------------------------------------- #
def test_delete_share_returns_true_when_row_removed() -> None:
    cur = FakeCursor(rowcounts=[1])
    with _patched(cur) as (conn, put_calls):
        out = share_store.delete_resource_share(
            "layer_shares", "layer_id", "r1", "g1",
        )
    assert out is True
    sql, params = cur.calls[0]
    assert "DELETE FROM layer_shares" in sql
    assert "layer_id = %s AND group_id = %s" in sql
    assert params == ("r1", "g1")
    assert conn.committed is True
    assert put_calls == [conn]


def test_delete_share_returns_false_when_nothing_to_delete() -> None:
    cur = FakeCursor(rowcounts=[0])
    with _patched(cur) as (conn, _):
        out = share_store.delete_resource_share(
            "layer_shares", "layer_id", "r1", "missing",
        )
    assert out is False
    assert conn.committed is True


# --------------------------------------------------------------------------- #
# replace_resource_shares
# --------------------------------------------------------------------------- #
def test_replace_shares_deletes_then_inserts_each_group() -> None:
    cur = FakeCursor()
    with _patched(cur) as (conn, put_calls):
        share_store.replace_resource_shares(
            "layer_shares", "layer_id", "r1", ["g1", "g2"],
        )
    # 1 delete + 2 inserts
    assert len(cur.calls) == 3
    del_sql, del_params = cur.calls[0]
    assert "DELETE FROM layer_shares WHERE layer_id = %s" in del_sql
    assert del_params == ("r1",)
    insert_sqls = [c for c in cur.calls[1:]]
    inserted_groups = {p[1] for _, p in insert_sqls}
    assert inserted_groups == {"g1", "g2"}
    for sql, params in insert_sqls:
        assert "INSERT INTO layer_shares" in sql
        assert "ON CONFLICT DO NOTHING" in sql
        assert params[0] == "r1"
    assert conn.committed is True
    assert put_calls == [conn]


def test_replace_shares_dedups_group_ids() -> None:
    cur = FakeCursor()
    with _patched(cur) as (conn, _):
        share_store.replace_resource_shares(
            "layer_shares", "layer_id", "r1", ["g1", "g1", "g1"],
        )
    # set() collapses duplicates -> 1 delete + 1 insert
    assert len(cur.calls) == 2


def test_replace_shares_empty_iterable_only_deletes() -> None:
    cur = FakeCursor()
    with _patched(cur) as (conn, put_calls):
        share_store.replace_resource_shares(
            "layer_shares", "layer_id", "r1", [],
        )
    assert len(cur.calls) == 1  # only the DELETE
    assert "DELETE FROM layer_shares" in cur.calls[0][0]
    assert conn.committed is True


def test_replace_shares_rolls_back_and_reraises_on_error() -> None:
    class BoomCursor(FakeCursor):
        def execute(self, sql: str, params: tuple = ()) -> None:
            raise RuntimeError("insert exploded")

    cur = BoomCursor()
    with _patched(cur) as (conn, put_calls):
        with pytest.raises(RuntimeError, match="insert exploded"):
            share_store.replace_resource_shares(
                "layer_shares", "layer_id", "r1", ["g1"],
            )
    assert conn.rolled_back is True
    assert conn.committed is False
    assert put_calls == [conn]  # connection still returned in finally


def test_replace_shares_returns_none() -> None:
    cur = FakeCursor()
    with _patched(cur):
        out = share_store.replace_resource_shares(
            "layer_shares", "layer_id", "r1", ["g1"],
        )
    assert out is None
