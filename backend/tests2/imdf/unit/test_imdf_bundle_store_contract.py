"""Contract tests for the IMDF bundle lifecycle store."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _imports(rel_path: str) -> set[str]:
    tree = ast.parse((_BACKEND_ROOT / rel_path).read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_bundle_store_deletes_local_and_remote(monkeypatch, tmp_path) -> None:
    from app.core.imdf import bundle_store

    local_dir = tmp_path / "task-1"
    local_dir.mkdir()
    (local_dir / "plenora_metadata.json").write_text("{}", encoding="utf-8")
    remote_calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        bundle_store.imdf_storage,
        "delete_bundle",
        lambda task_id, *, cleanup_local: remote_calls.append((task_id, cleanup_local)) or 2,
    )

    assert bundle_store.delete_bundle(task_id="task-1", local_dir=local_dir) == 2
    assert not local_dir.exists()
    assert remote_calls == [("task-1", False)]


def test_bundle_store_deletes_local_when_remote_cleanup_fails(
    monkeypatch,
    tmp_path,
) -> None:
    from app.core.imdf import bundle_store

    local_dir = tmp_path / "task-1"
    local_dir.mkdir()

    def fail_remote(_task_id: str, *, cleanup_local: bool) -> int:
        raise RuntimeError("storage disabled")

    monkeypatch.setattr(bundle_store.imdf_storage, "delete_bundle", fail_remote)

    assert bundle_store.delete_bundle(task_id="task-1", local_dir=local_dir) == 0
    assert not local_dir.exists()


def test_bundle_store_purges_only_expired_local_bundles(monkeypatch, tmp_path):
    import os
    from app.core.imdf import bundle_store

    now = 2_000_000.0
    fresh = tmp_path / "fresh"
    expired = tmp_path / "expired"
    fresh.mkdir()
    expired.mkdir()
    os.utime(fresh, (now - 60, now - 60))
    os.utime(expired, (now - 48 * 3600 - 10, now - 48 * 3600 - 10))

    deleted: list[str] = []
    monkeypatch.setattr(
        bundle_store,
        "delete_bundle",
        lambda *, task_id, local_dir: deleted.append(task_id) or local_dir.rmdir() or 0,
    )

    removed = bundle_store.purge_expired_local_bundles(
        bundles_dir=tmp_path,
        now_ts=now,
        ttl_seconds=48 * 3600,
    )

    assert removed == 1
    assert deleted == ["expired"]
    assert fresh.exists()
    assert not expired.exists()


def test_bundle_store_resolves_local_bundle_sidecars_and_gdb(tmp_path) -> None:
    from app.core.imdf import bundle_store

    bundle_dir = tmp_path / "task-1"
    bundle_dir.mkdir()
    gdb_dir = bundle_dir / "site.gdb"
    gdb_dir.mkdir()
    (bundle_dir / "plenora_metadata.json").write_text(
        '{"project_id": "project-1", "site_id": "site-1"}',
        encoding="utf-8",
    )
    (bundle_dir / "renderers.json").write_text('{"layers": {}}', encoding="utf-8")

    assert bundle_store.resolve_bundle_dir("task-1", root=tmp_path) == bundle_dir
    assert bundle_store.ensure_local_bundle("task-1", root=tmp_path) == bundle_dir
    assert bundle_store.find_gdb_dir(bundle_dir) == gdb_dir
    assert bundle_store.read_metadata(bundle_dir) == {
        "project_id": "project-1",
        "site_id": "site-1",
    }
    assert bundle_store.read_renderers(bundle_dir) == {"layers": {}}


def test_bundle_store_hydrates_missing_local_bundle(monkeypatch, tmp_path) -> None:
    from app.core.imdf import bundle_store

    hydrated_dir = tmp_path / "hydrated"
    hydrated_dir.mkdir()
    calls: list[str] = []

    def fake_ensure(task_id: str):
        calls.append(task_id)
        return hydrated_dir

    monkeypatch.setattr(bundle_store.imdf_storage, "ensure_local_bundle", fake_ensure)

    assert bundle_store.ensure_local_bundle("task-1", root=tmp_path) == hydrated_dir
    assert calls == ["task-1"]


def test_bundle_store_remote_metadata_and_task_listing_delegate_to_storage(monkeypatch) -> None:
    from app.core.imdf import bundle_store

    monkeypatch.setattr(
        bundle_store.imdf_storage,
        "get_meta_only",
        lambda task_id: {"task_id": task_id, "project_id": "project-1"},
    )
    monkeypatch.setattr(
        bundle_store.imdf_storage,
        "list_task_ids",
        lambda: ["task-1", "task-2"],
    )

    assert bundle_store.read_remote_metadata("task-1") == {
        "task_id": "task-1",
        "project_id": "project-1",
    }
    assert bundle_store.list_task_ids() == ["task-1", "task-2"]


def test_imdf_router_delegates_delete_to_bundle_store() -> None:
    router_imports = _imports("app/core/imdf/router.py")
    router_src = (_BACKEND_ROOT / "app/core/imdf/router.py").read_text(encoding="utf-8")

    assert "app.core.imdf" in router_imports
    assert "shutil" not in router_imports
    assert "rmtree(" not in router_src
    assert "_imdf_bundle_store.delete_bundle" in router_src


def test_imdf_publish_and_validator_delegate_bundle_resolution_to_store() -> None:
    publish_src = (_BACKEND_ROOT / "workers/tasks/imdf_publish_task.py").read_text(
        encoding="utf-8"
    )
    validator_src = (_BACKEND_ROOT / "app/core/imdf/validator.py").read_text(
        encoding="utf-8"
    )

    assert "bundle_store.ensure_local_bundle" in publish_src
    assert "bundle_store.find_gdb_dir" in publish_src
    assert '.rglob("*.gdb")' not in publish_src
    assert "bundle_store.ensure_local_bundle" in validator_src
    assert "bundle_store.find_gdb_dir" in validator_src
    assert "validate_imdf_task_id" not in validator_src
