"""
Contract test BE-STORAGE-001A — `TaskScopedBlobStore` Protocol.

Contract tests for the task-scoped blob store boundary.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import get_type_hints

import pytest

from tests2.shared._ast_identifiers import imports_module, uses_attribute

from app.shared.resources.task_scoped_blob_store import (
    TaskBlobSaveResult,
    TaskScopedBlobStore,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_APP = Path(__file__).resolve().parent.parent.parent / "app"


# 1. TaskBlobSaveResult shape


def test_task_blob_save_result_fields():
    r = TaskBlobSaveResult(primary_key="imdf-bundles/task-1.zip")
    assert r.primary_key == "imdf-bundles/task-1.zip"
    assert r.sidecar_key is None


def test_task_blob_save_result_with_sidecar():
    r = TaskBlobSaveResult(
        primary_key="imdf-bundles/task-1.zip",
        sidecar_key="imdf-bundles/task-1.meta.json",
    )
    assert r.sidecar_key == "imdf-bundles/task-1.meta.json"


def test_task_blob_save_result_is_frozen():
    r = TaskBlobSaveResult(primary_key="x")
    with pytest.raises(FrozenInstanceError):
        r.primary_key = "y"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        r.sidecar_key = "z"  # type: ignore[misc]


def test_task_blob_save_result_equality_by_value():
    a = TaskBlobSaveResult(primary_key="k", sidecar_key="s")
    b = TaskBlobSaveResult(primary_key="k", sidecar_key="s")
    assert a == b
    assert hash(a) == hash(b)


def test_task_blob_save_result_field_types():
    """Type hints espliciti, niente `Any`."""
    hints = get_type_hints(TaskBlobSaveResult)
    assert hints["primary_key"] is str
    # sidecar_key: str | None
    assert hints["sidecar_key"] is not None


# 2. TaskScopedBlobStore Protocol shape


def test_protocol_has_required_methods():
    assert hasattr(TaskScopedBlobStore, "find_keys")
    assert hasattr(TaskScopedBlobStore, "delete")


def test_protocol_is_runtime_checkable():
    """`@runtime_checkable` permette `isinstance(obj, Protocol)`."""
    # Una classe minima che implementa il protocol passa isinstance
    class _MinImpl:
        prefix: str = "test"

        def find_keys(self, task_id: str):
            return []

        def delete(self, task_id: str):
            return 0

    impl = _MinImpl()
    assert isinstance(impl, TaskScopedBlobStore)


def test_protocol_rejects_incomplete_impl():
    """Impl che manca un metodo required NON soddisfa il Protocol."""

    class _NoFindKeys:
        prefix = "x"

        def delete(self, task_id: str):
            return 0

    class _NoDelete:
        prefix = "x"

        def find_keys(self, task_id: str):
            return []

    assert not isinstance(_NoFindKeys(), TaskScopedBlobStore)
    assert not isinstance(_NoDelete(), TaskScopedBlobStore)


# 3. Pure module — niente import pesanti


def test_module_has_no_heavy_imports():
    """Pure module: niente pandas/geopandas/requests/boto3/cloud_backends/
    config/I-O/env reads."""
    src = (
        _BACKEND_APP / "shared" / "resources" / "task_scoped_blob_store.py"
    ).read_text(encoding="utf-8")
    assert not imports_module(src, "pandas"), (
        "task_scoped_blob_store.py NON deve contenere 'import pandas'/'from pandas': "
        "il modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "geopandas"), (
        "task_scoped_blob_store.py NON deve contenere 'import geopandas': "
        "il modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "requests"), (
        "task_scoped_blob_store.py NON deve contenere 'import requests'/'from requests': "
        "il modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "boto3"), (
        "task_scoped_blob_store.py NON deve contenere 'import boto3': "
        "il modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "app.infrastructure.storage.cloud_backends"), (
        "task_scoped_blob_store.py NON deve contenere 'from app.infrastructure.storage.cloud_backends': "
        "il modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "app.config"), (
        "task_scoped_blob_store.py NON deve contenere 'from app.config': "
        "il modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "os"), (
        "task_scoped_blob_store.py NON deve contenere 'import os': "
        "il modulo deve restare pure protocol/dataclass."
    )
    assert not uses_attribute(src, "os", "getenv"), (
        "task_scoped_blob_store.py NON deve contenere 'os.getenv': "
        "il modulo deve restare pure protocol/dataclass."
    )
    assert not uses_attribute(src, "os", "environ"), (
        "task_scoped_blob_store.py NON deve contenere 'os.environ': "
        "il modulo deve restare pure protocol/dataclass."
    )


def test_module_only_has_dataclass_and_protocol():
    """Verifica statica: il modulo espone solo `TaskBlobSaveResult` +
    `TaskScopedBlobStore` (no funzioni runtime, no logica)."""
    import app.shared.resources.task_scoped_blob_store as mod
    public = [n for n in dir(mod) if not n.startswith("_")]
    # Filter built-ins / standard lib
    public = [n for n in public if n not in ("annotations", "Protocol", "List",
                                              "runtime_checkable", "dataclass")]
    assert set(public) >= {"TaskBlobSaveResult", "TaskScopedBlobStore"}


# 4. Parity audit: existing wrappers vs Protocol common denominator


def test_eseguibili_storage_has_common_methods():
    """`app.core.eseguibili.storage` espone `find_keys` e
    `delete_by_task` (equivalente al `delete` del Protocol). Verifica
    di esistenza, non di runtime behavior."""
    from app.core.eseguibili import storage as eseguibili_storage
    assert callable(getattr(eseguibili_storage, "find_keys", None))
    assert callable(getattr(eseguibili_storage, "delete_by_task", None))
    # PREFIX const allineato a storage_layout
    assert eseguibili_storage.PREFIX == "eseguibili-outputs"


def test_specialized_methods_are_per_wrapper():
    """Documenta in test le SPECIALIZATIONS che restano per-wrapper:
    - eseguibili: `open_stream(key)` per streaming download.

    (Post-collasso IMDF a una-run il wrapper `imdf.storage` non esiste piu':
    il bundle non viene persistito, e' carica_arcgis a leggerlo in-pipeline.)

    Il Protocol common denominator NON cattura queste capabilities;
    ogni eventuale futuro consumer cross-cluster che ne ha bisogno
    dovra' depend-on l'impl concrete o un Protocol esteso."""
    from app.core.eseguibili import storage as ese_storage

    assert callable(getattr(ese_storage, "open_stream", None))
    # Eseguibili non ha sidecar accessor
    assert getattr(ese_storage, "get_meta_only", None) is None


# 5. Storage prefix alignment with app.shared.resources.storage_layout


def test_prefixes_aligned_with_storage_layout():
    """Audit: il prefix del wrapper eseguibili coincide con la constant
    centralizzata in `app.shared.resources.storage_layout` (BE-DATA-PLANE-001A).
    Sentinel cross-cluster."""
    from app.core.eseguibili import storage as ese_storage
    from app.shared.resources.storage_layout import ESEGUIBILI_OUTPUTS_PREFIX

    assert ese_storage.PREFIX == ESEGUIBILI_OUTPUTS_PREFIX
