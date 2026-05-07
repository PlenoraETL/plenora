"""
Small test helpers for `ResultBlobLoader` contract tests.

BE-GO-READY-TEST-INFRA-003-RESOURCE-TEST-SLIMMING.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


def make_result_ref(
    *,
    result_id: str = "r1",
    group_id: str = "g1",
    project_id: Optional[str] = None,
    is_superadmin: bool = False,
):
    """Build a `ResourceRef(kind='result_blob', ...)` with defaults.

    Defaults match the most common shape across the contract
    suites: `result_id='r1'`, `expected_group_id='g1'`,
    `expected_project_id=None`, `is_superadmin=False`. Any test
    that needs a specific value passes it explicitly.
    """
    from app.core.resources import ResourceRef

    return ResourceRef(
        kind="result_blob",
        expected_group_id=group_id,
        result_id=result_id,
        is_superadmin=is_superadmin,
        expected_project_id=project_id,
    )


def fake_fetchone(
    *, row: Optional[dict] = None
) -> tuple[dict, Callable[..., Optional[dict]]]:
    """Return `(captured, fn)` where `fn(sql, params=())` records
    `sql` + `params` into the dict and returns `row`.

    `row=None` simulates "row not found".

    Use:

        captured, fake = fake_fetchone(row={"data": b"x", "storage_key": None})
        with patch("app.db.pg_connection.fetchone", fake):
            ...
        assert captured["params"] == ("r1",)
    """
    captured: dict[str, Any] = {}

    def _impl(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return row

    return captured, _impl


def fake_dispatch_by_id(
    *,
    payload: Optional[bytes] = b"x",
    backend: str = "db_legacy",
) -> tuple[dict, Callable[..., tuple]]:
    """Return `(captured, fn)` where
    `fn(result_id, *, group_id_filter, project_id_filter=None)`
    records each kwarg and returns `(payload, backend)`.

    Defaults: `payload=b"x"`, `backend="db_legacy"` (the most
    common happy-path return shape across the contract suites).

    Use:

        captured, fake = fake_dispatch_by_id()
        with patch("app.core.resources.loaders.result_blob._dispatch_by_id", fake):
            loader.load(ref, mode="bytes")
        assert captured["group_id_filter"] == "g1"
    """
    captured: dict[str, Any] = {}

    def _impl(result_id, *, group_id_filter, project_id_filter=None):
        captured["result_id"] = result_id
        captured["group_id_filter"] = group_id_filter
        captured["project_id_filter"] = project_id_filter
        return payload, backend

    return captured, _impl


def cleanup_resource_path(resource) -> None:
    """Best-effort unlink for a path-mode `Resource`. Safe to call
    with `resource=None` or with a Resource whose `path` is None.

    Replaces the repeated `try/finally: if out.path and out.path.exists():
    out.path.unlink()` boilerplate.
    """
    if resource is None:
        return
    path = getattr(resource, "path", None)
    if path is not None and path.exists():
        path.unlink()
