"""Contract test Phase 6 Batch 1.5 — object storage capability globale.

Verifica:
  A. object_storage_enabled() rispetta OBJECT_STORAGE_ENABLED + BACKEND
  B. get_runtime_object_storage / get_backup_object_storage:
     - sollevano ObjectStorageDisabledError quando capability off
     - usano bucket distinti
  C. result_store / file_service / file_sources / eseguibili / IMDF
     non tentano upload/list S3 quando capability off
  D. admin_backups list ritorna feature-disabled (no boto3 import)
     quando capability off
  E. compat: get_object_storage delega a get_runtime_object_storage
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# ============================================================
# A. object_storage_enabled
# ============================================================


def _patch_capability(enabled, backend):
    """Sovrascrive lettura settings/provider per il test."""
    return patch(
        "app.shared.cloud_backends._read_object_storage_capability",
        return_value={"enabled": enabled, "backend": backend},
    )


def test_object_storage_enabled_true_when_settings_say_so():
    from app.shared import cloud_backends as cb
    with _patch_capability(True, "s3"):
        assert cb.object_storage_enabled() is True


def test_object_storage_enabled_false_when_disabled_flag():
    from app.shared import cloud_backends as cb
    with _patch_capability(False, "s3"):
        assert cb.object_storage_enabled() is False


def test_object_storage_enabled_false_when_backend_none():
    from app.shared import cloud_backends as cb
    with _patch_capability(True, "none"):
        assert cb.object_storage_enabled() is False


# ============================================================
# B. Runtime / backup factories
# ============================================================


def test_runtime_factory_raises_when_disabled():
    from app.shared import cloud_backends as cb
    with _patch_capability(False, "s3"):
        with pytest.raises(cb.ObjectStorageDisabledError):
            cb.get_runtime_object_storage()


def test_backup_factory_raises_when_disabled():
    from app.shared import cloud_backends as cb
    with _patch_capability(False, "s3"):
        with pytest.raises(cb.ObjectStorageDisabledError):
            cb.get_backup_object_storage()


def test_runtime_factory_uses_object_storage_bucket(monkeypatch):
    """OBJECT_STORAGE_BUCKET vince su PLENORA_ARTIFACT_BUCKET e BACKUP_BUCKET."""
    from app.shared import cloud_backends as cb

    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "runtime-bucket")
    monkeypatch.setenv("PLENORA_ARTIFACT_BUCKET", "artifact-bucket")
    monkeypatch.setenv("BACKUP_BUCKET", "backup-bucket")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")

    captured = {}

    def _fake_build(bucket, *, region_default):
        captured["bucket"] = bucket
        return MagicMock()

    with _patch_capability(True, "s3"), patch.object(
        cb, "_build_object_storage", side_effect=_fake_build,
    ):
        cb.get_runtime_object_storage()

    assert captured["bucket"] == "runtime-bucket"


def test_runtime_factory_falls_back_to_plenora_artifact_bucket(monkeypatch):
    from app.shared import cloud_backends as cb

    monkeypatch.delenv("OBJECT_STORAGE_BUCKET", raising=False)
    monkeypatch.setenv("PLENORA_ARTIFACT_BUCKET", "artifact-bucket")
    monkeypatch.setenv("BACKUP_BUCKET", "backup-bucket")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")

    captured = {}

    def _fake_build(bucket, *, region_default):
        captured["bucket"] = bucket
        return MagicMock()

    with _patch_capability(True, "s3"), patch.object(
        cb, "_build_object_storage", side_effect=_fake_build,
    ):
        cb.get_runtime_object_storage()

    assert captured["bucket"] == "artifact-bucket"


def test_runtime_factory_falls_back_to_backup_bucket_with_warning(monkeypatch):
    """Retro-compat: se solo BACKUP_BUCKET e' settato, runtime cade su
    quello con warning. Il warning serve a operations per migrare alla
    config canonica (OBJECT_STORAGE_BUCKET distinto)."""
    from app.shared import cloud_backends as cb

    monkeypatch.delenv("OBJECT_STORAGE_BUCKET", raising=False)
    monkeypatch.setenv("PLENORA_ARTIFACT_BUCKET", "")
    monkeypatch.setenv("BACKUP_BUCKET", "backup-bucket")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")

    captured = {}

    def _fake_build(bucket, *, region_default):
        captured["bucket"] = bucket
        return MagicMock()

    warn_spy = MagicMock()
    monkeypatch.setattr(cb.logger, "warning", warn_spy)

    with _patch_capability(True, "s3"), patch.object(
        cb, "_build_object_storage", side_effect=_fake_build,
    ):
        cb.get_runtime_object_storage()

    assert captured["bucket"] == "backup-bucket"
    # Almeno un warning deve menzionare BACKUP_BUCKET / retro-compat.
    warn_args = " ".join(
        " ".join(str(a) for a in call.args) for call in warn_spy.call_args_list
    )
    assert "BACKUP_BUCKET" in warn_args
    assert "retro-compat" in warn_args


def test_backup_factory_uses_backup_bucket_only(monkeypatch):
    """get_backup_object_storage NON cade mai su OBJECT_STORAGE_BUCKET."""
    from app.shared import cloud_backends as cb

    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "runtime-bucket")
    monkeypatch.delenv("BACKUP_BUCKET", raising=False)
    monkeypatch.delenv("BACKUP_S3_BUCKET", raising=False)

    with _patch_capability(True, "s3"):
        with pytest.raises(ValueError, match="BACKUP_BUCKET"):
            cb.get_backup_object_storage()


def test_backup_factory_returns_backup_bucket(monkeypatch):
    from app.shared import cloud_backends as cb

    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "runtime-bucket")
    monkeypatch.setenv("BACKUP_BUCKET", "backup-bucket")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")

    captured = {}

    def _fake_build(bucket, *, region_default):
        captured["bucket"] = bucket
        return MagicMock()

    with _patch_capability(True, "s3"), patch.object(
        cb, "_build_object_storage", side_effect=_fake_build,
    ):
        cb.get_backup_object_storage()

    assert captured["bucket"] == "backup-bucket"


# ============================================================
# C. Runtime callsite guards
# ============================================================


def test_results_db_service_s3_download_returns_none_when_disabled():
    """Capability off -> _s3_download_bytes ritorna None senza tentare
    upload/get_object_storage."""
    from app.db import results_db_service

    with _patch_capability(False, "s3"):
        out = results_db_service._s3_download_bytes("results/foo/bar.bin")
    assert out is None


def test_results_db_service_s3_delete_silent_when_disabled():
    """Capability off -> _s3_delete_best_effort no-op silenzioso."""
    from app.db import results_db_service
    # No raise, no warning logged from cloud_backends-side; pass smoke.
    with _patch_capability(False, "s3"):
        results_db_service._s3_delete_best_effort("results/foo/bar.bin")


def test_file_sources_delete_skips_s3_cleanup_when_disabled(monkeypatch):
    """delete_file_source con storage_key + capability off: nessun
    tentativo di eliminare su S3, DELETE DB procede."""
    from app.db.config import file_sources

    fake_row = {"storage_key": "file-sources/abc/xxx"}
    monkeypatch.setattr(
        file_sources, "fetchone", lambda *a, **kw: fake_row,
    )

    fake_cur = MagicMock()
    fake_cur.rowcount = 1
    monkeypatch.setattr(
        file_sources, "execute", lambda *a, **kw: fake_cur,
    )

    runtime_called = {"n": 0}

    def _fake_runtime():
        runtime_called["n"] += 1
        raise AssertionError("get_runtime_object_storage NON deve essere chiamato")

    with _patch_capability(False, "s3"), patch(
        "app.shared.cloud_backends.get_runtime_object_storage",
        side_effect=_fake_runtime,
    ):
        ok = file_sources.delete_file_source("abc")

    assert ok is True
    assert runtime_called["n"] == 0


def test_imdf_storage_list_task_ids_returns_empty_when_disabled():
    from app.core.imdf import storage as imdf_storage
    with _patch_capability(False, "s3"):
        assert imdf_storage.list_task_ids() == []


def test_imdf_storage_get_meta_only_returns_none_when_disabled():
    from app.core.imdf import storage as imdf_storage
    with _patch_capability(False, "s3"):
        assert imdf_storage.get_meta_only("any-task") is None


# ============================================================
# D. Admin backups
# ============================================================


def test_admin_backups_list_raises_disabled_without_touching_runtime_factory():
    """`OBJECT_STORAGE_ENABLED=false` -> AdminBackupDisabledError.

    La capability off solleva PRIMA che list_backups arrivi a costruire
    l'ObjectStorage o a fare list_objects_v2; di conseguenza nessun
    boto3 client viene istanziato dal path admin_backups (la chiamata
    fallisce nella factory `get_backup_object_storage`).
    """
    from app.core.admin_backups import service as admin_service
    from app.shared import cloud_backends as cb

    # Spy: se la capability e' off, _build_object_storage NON deve essere
    # invocato (e' il punto in cui il client boto3 viene costruito).
    build_spy = MagicMock(side_effect=AssertionError(
        "Build object storage NON deve essere chiamato a capability off"
    ))

    with _patch_capability(False, "s3"), patch.object(
        cb, "_build_object_storage", build_spy,
    ):
        with pytest.raises(admin_service.AdminBackupDisabledError):
            admin_service.list_backups()

    build_spy.assert_not_called()


# ============================================================
# E. Compat get_object_storage
# ============================================================


def test_get_object_storage_delegates_to_runtime():
    """Compat shim: get_object_storage ritorna l'identica istanza di
    get_runtime_object_storage."""
    from app.shared import cloud_backends as cb

    sentinel = MagicMock()
    with patch.object(cb, "get_runtime_object_storage", return_value=sentinel):
        out = cb.get_object_storage()

    assert out is sentinel


def test_get_object_storage_propagates_disabled_error():
    """Compat shim non deve nascondere ObjectStorageDisabledError."""
    from app.shared import cloud_backends as cb

    with _patch_capability(False, "s3"):
        with pytest.raises(cb.ObjectStorageDisabledError):
            cb.get_object_storage()
