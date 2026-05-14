"""Contract tests for the IMDF publish dispatch boundary."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.imdf.publish_dispatcher import (
    IMDF_PUBLISH_TASK_NAME,
    enqueue_imdf_publish,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


class FakeImdfPublishTask:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def delay(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="imdf-publish-1")


class FakeImdfPublishRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_task(self, name, **kwargs):
        self.calls.append({"name": name, **kwargs})
        return SimpleNamespace(id="imdf-runtime-1")


def _imports(rel_path: str) -> set[str]:
    tree = ast.parse((_BACKEND_ROOT / rel_path).read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_enqueue_imdf_publish_adapts_to_runtime_shape() -> None:
    task = FakeImdfPublishTask()

    handle = enqueue_imdf_publish(
        bundle_task_id="bundle-1",
        publisher_config={"mode": "replace", "fs_name": "site"},
        task=task,
    )

    assert handle.id == "imdf-publish-1"
    assert task.calls == [
        {
            "bundle_task_id": "bundle-1",
            "publisher_config": {"mode": "replace", "fs_name": "site"},
        }
    ]


def test_enqueue_imdf_publish_dispatches_by_task_name_through_runtime() -> None:
    runtime = FakeImdfPublishRuntime()

    handle = enqueue_imdf_publish(
        bundle_task_id="bundle-1",
        publisher_config={"mode": "replace", "fs_name": "site"},
        runtime=runtime,
    )

    assert handle.id == "imdf-runtime-1"
    assert runtime.calls == [
        {
            "name": IMDF_PUBLISH_TASK_NAME,
            "kwargs": {
                "bundle_task_id": "bundle-1",
                "publisher_config": {"mode": "replace", "fs_name": "site"},
            },
        }
    ]


def test_imdf_router_uses_publish_dispatch_boundary() -> None:
    imdf_root = _BACKEND_ROOT / "app" / "core" / "imdf"
    checked_paths = [
        path
        for path in imdf_root.rglob("*.py")
        if path.name != "publish_dispatcher.py"
    ]
    router_imports = _imports("app/core/imdf/router.py")

    assert "app.core.imdf.publish_dispatcher" in router_imports
    for path in checked_paths:
        imports = _imports(path.relative_to(_BACKEND_ROOT).as_posix())
        assert "workers.tasks.imdf_publish_task" not in imports, path


def test_imdf_publish_dispatcher_does_not_import_concrete_task_module() -> None:
    dispatcher_imports = _imports("app/core/imdf/publish_dispatcher.py")

    assert "workers.tasks.imdf_publish_task" not in dispatcher_imports
    assert "workers.celery_app" not in dispatcher_imports


def test_imdf_publish_dispatcher_uses_shared_task_runtime_boundary() -> None:
    dispatcher_imports = _imports("app/core/imdf/publish_dispatcher.py")

    assert "app.core.task_runtime.dispatch" in dispatcher_imports
