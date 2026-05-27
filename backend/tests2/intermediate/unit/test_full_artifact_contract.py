"""
Contract test Batch object storage intermedi (2026-04-28).

Verifica:
"""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.intermediate, pytest.mark.contract]


# Helpers


def _settings_baseline(**overrides) -> dict:
    base = {
        "mode": "metadata_only",
        "preview_rows": 500,
        "artifact_format": "parquet",
        "manipola_checkpoints": "critical",
        "estrai_checkpoints": "final_only",
        "arricchisci_checkpoints": "final_only",
        "artifact_bucket": None,
        "artifact_prefix": "intermediate",
        "fingerprint_mode": "sample",
        "fingerprint_sample_rows": 1000,
    }
    base.update(overrides)
    return base


def _patch_settings(**overrides):
    from app.core.intermediate import policy as _policy
    from app.core.intermediate.policy import reset_validation_cache

    cfg = _settings_baseline(**overrides)
    reset_validation_cache()
    return patch.object(_policy, "_read_settings", return_value=cfg)


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    from app.core.intermediate.policy import reset_validation_cache
    reset_validation_cache()
    yield
    reset_validation_cache()


@contextmanager
def _mock_object_storage(uploads: dict):
    """Patcha `get_runtime_object_storage` con un fake `upload_bytes`
    che cattura i dati uploadati. Anche `object_storage_enabled` -> True."""
    fake_storage = MagicMock(name="runtime_storage")

    def _capture_upload_bytes(data, key):
        uploads[key] = data

    fake_storage.upload_bytes.side_effect = _capture_upload_bytes

    with patch(
        "app.core.io.object_storage.get_runtime_object_storage",
        return_value=fake_storage,
    ), patch(
        "app.core.io.object_storage.object_storage_enabled",
        return_value=True,
    ):
        yield fake_storage


def _patch_run_meta(canvas_id: str = "proj-1", group_id: str = "grp-1"):
    return patch(
        "app.core.intermediate.runs_db.get_run",
        return_value={
            "canvas_id": canvas_id,
            "group_id": group_id,
            "run_id": "run-1",
        },
    )


# A. Default mode = metadata_only


def test_default_persistence_mode_is_metadata_only():
    from app.config import settings
    assert settings.get("INTERMEDIATE_PERSISTENCE_MODE") == "metadata_only"


# B. mode=full_artifact: parquet + manifest + meta


def test_full_artifact_writes_parquet_and_manifest_to_runtime_object_storage():
    from app.core.intermediate import snapshot as _snap

    df = pd.DataFrame({"a": list(range(50)), "b": ["x"] * 50})
    captured_db = {}

    def _capture_db(**kwargs):
        captured_db.update(kwargs)

    uploads: dict = {}
    with _patch_settings(mode="full_artifact"), _mock_object_storage(uploads), \
            _patch_run_meta(canvas_id="proj-FA", group_id="grp-FA"), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic",
        side_effect=_capture_db,
    ):
        handle = _snap.record_final_snapshot(
            run_id="run-FA",
            module="manipola",
            node_id="node-FA",
            node_type="result",
            df=df,
        )

    # Due upload: data.parquet + manifest.json
    keys = list(uploads.keys())
    assert any(k.endswith("/data.parquet") for k in keys), keys
    assert any(k.endswith("/manifest.json") for k in keys), keys

    # Layout deterministico
    expected_prefix = (
        "intermediate/group=grp-FA/project=proj-FA/run=run-FA/"
        "module=manipola/node=node-FA/kind=final/"
    )
    assert any(k == expected_prefix + "data.parquet" for k in keys), keys
    assert any(k == expected_prefix + "manifest.json" for k in keys), keys

    # Manifest valido + campi attesi
    manifest_key = expected_prefix + "manifest.json"
    manifest = json.loads(uploads[manifest_key].decode("utf-8"))
    assert manifest["artifact_version"] == 1
    assert manifest["run_id"] == "run-FA"
    assert manifest["project_id"] == "proj-FA"
    assert manifest["group_id"] == "grp-FA"
    assert manifest["module"] == "manipola"
    assert manifest["node_id"] == "node-FA"
    assert manifest["node_type"] == "result"
    assert manifest["snapshot_kind"] == "final"
    assert manifest["format"] == "parquet"
    assert manifest["row_count"] == 50
    assert manifest["column_count"] == 2
    assert manifest["node_hash"]
    assert manifest["hash_algo"] in ("pandas_sample_hash_v1", "pandas_hash_v1")

    # node_meta: artifact_uri/status/format valorizzati, zero rows
    assert captured_db["rows_to_persist"] == []
    assert captured_db["row_count"] == 50
    assert captured_db["persistence_mode"] == "full_artifact"
    assert captured_db["artifact_uri"] == expected_prefix + "data.parquet"
    assert captured_db["artifact_format"] == "parquet"
    assert captured_db["artifact_status"] == "complete"
    assert captured_db["artifact_error"] is None

    # SnapshotHandle reflects status
    assert handle.recorded is True
    assert handle.persistence_mode == "full_artifact"
    assert handle.artifact_status == "complete"
    assert handle.persisted_rows_count == 0


def test_full_artifact_parquet_payload_roundtrip():
    """Smoke: il Parquet bytes uploadato e' deserializzabile e mantiene
    schema + cardinalita'."""
    from app.core.intermediate import snapshot as _snap

    df = pd.DataFrame({"a": [1, 2, 3, 4], "b": ["w", "x", "y", "z"]})
    uploads: dict = {}

    with _patch_settings(mode="full_artifact"), _mock_object_storage(uploads), \
            _patch_run_meta(), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic"
    ):
        _snap.record_final_snapshot(
            run_id="run-r",
            module="manipola",
            node_id="node-r",
            node_type="result",
            df=df,
        )

    parquet_key = next(k for k in uploads if k.endswith("/data.parquet"))
    roundtrip = pd.read_parquet(io.BytesIO(uploads[parquet_key]))
    assert list(roundtrip.columns) == ["a", "b"]
    assert len(roundtrip) == 4
    assert roundtrip["a"].tolist() == [1, 2, 3, 4]


# C. Object storage disabled


def test_full_artifact_validate_fails_when_object_storage_disabled():
    """OBJECT_STORAGE_ENABLED=false + mode=full_artifact -> validate
    solleva con messaggio operativo."""
    from app.core.intermediate.policy import (
        IntermediateSettingsError, validate_intermediate_settings,
    )
    cfg = _settings_baseline(mode="full_artifact")
    with patch(
        "app.core.io.object_storage.object_storage_enabled",
        return_value=False,
    ):
        with pytest.raises(IntermediateSettingsError, match="OBJECT_STORAGE_ENABLED"):
            validate_intermediate_settings(cfg)


def test_full_artifact_runtime_capability_collapse_marks_failed():
    """Se la validate passa ma la capability collassa a runtime
    (env mutato, secrets ruotati), `persist_full_artifact` ritorna
    `failed` senza crashare la run. node_meta riflette il fatto."""
    from app.core.intermediate import snapshot as _snap
    from app.infrastructure.storage.cloud_backends import ObjectStorageDisabledError

    df = pd.DataFrame({"a": [1, 2, 3]})
    captured_db = {}

    def _capture_db(**kwargs):
        captured_db.update(kwargs)

    # validate gia' passata (mode=full_artifact valido) ma a runtime
    # `get_runtime_object_storage` solleva.
    fake_get = MagicMock(side_effect=ObjectStorageDisabledError("disabled"))

    with _patch_settings(mode="full_artifact"), patch(
        "app.core.io.object_storage.object_storage_enabled",
        return_value=True,  # validate OK
    ), patch(
        "app.core.io.object_storage.get_runtime_object_storage", fake_get,
    ), _patch_run_meta(), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic",
        side_effect=_capture_db,
    ):
        handle = _snap.record_final_snapshot(
            run_id="run-x", module="manipola",
            node_id="node-x", node_type="result", df=df,
        )

    assert handle.artifact_status == "failed"
    assert captured_db["artifact_uri"] is None
    assert captured_db["artifact_status"] == "failed"
    assert "disabled" in (captured_db["artifact_error"] or "")


# D. Layout key deterministico


def test_artifact_layout_key_is_deterministic_and_well_formed():
    from app.core.intermediate.artifact_writer import build_artifact_key_prefix

    key = build_artifact_key_prefix(
        prefix="intermediate",
        group_id="g",
        project_id="p",
        run_id="r",
        module="manipola",
        node_id="n",
        snapshot_kind="checkpoint",
    )
    assert key == "intermediate/group=g/project=p/run=r/module=manipola/node=n/kind=checkpoint/"


def test_artifact_layout_rejects_slash_in_identifier():
    from app.core.intermediate.artifact_writer import build_artifact_key_prefix

    with pytest.raises(ValueError, match="non valido"):
        build_artifact_key_prefix(
            prefix="intermediate", group_id="g", project_id="p/x",
            run_id="r", module="manipola", node_id="n",
            snapshot_kind="checkpoint",
        )


def test_artifact_layout_strips_trailing_slash_from_prefix():
    from app.core.intermediate.artifact_writer import build_artifact_key_prefix

    key = build_artifact_key_prefix(
        prefix="intermediate///",
        group_id="g", project_id="p", run_id="r",
        module="manipola", node_id="n", snapshot_kind="final",
    )
    assert key.startswith("intermediate/group=g/")
    assert "//" not in key.replace("kind=", "k=")


# E. Niente df.to_dict full in mode full_artifact


def test_full_artifact_does_not_call_to_dict_full():
    """Anti-regression: il path full_artifact NON deve chiamare
    `df.to_dict(orient='records')` (vincolo del batch). Solo
    `df.to_parquet(buf)` su BytesIO."""
    from app.core.intermediate import snapshot as _snap

    real_df = pd.DataFrame({"a": list(range(2000))})

    spy_to_dict = MagicMock(side_effect=AssertionError(
        "to_dict NON deve essere chiamato in mode full_artifact"
    ))

    uploads: dict = {}
    with patch.object(pd.DataFrame, "to_dict", spy_to_dict), \
         _patch_settings(mode="full_artifact"), \
         _mock_object_storage(uploads), \
         _patch_run_meta(), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic",
    ):
        _snap.record_final_snapshot(
            run_id="run-no-dict", module="manipola",
            node_id="node-no-dict", node_type="result", df=real_df,
        )

    spy_to_dict.assert_not_called()


# F. write_node legacy / full_pg invariato


def test_full_pg_legacy_path_does_not_touch_object_storage():
    """In mode `full_pg`, `_record_snapshot` continua a scrivere righe
    in node_rows e NON invoca `persist_full_artifact` ne'
    `get_runtime_object_storage`. Anti-regression sul path legacy."""
    from app.core.intermediate import snapshot as _snap

    df = pd.DataFrame({"a": [1, 2, 3]})

    fake_get_storage = MagicMock(side_effect=AssertionError(
        "get_runtime_object_storage NON deve essere chiamato in full_pg"
    ))
    captured_db = {}

    def _capture_db(**kwargs):
        captured_db.update(kwargs)

    with _patch_settings(mode="full_pg"), patch(
        "app.core.io.object_storage.get_runtime_object_storage", fake_get_storage,
    ), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic",
        side_effect=_capture_db,
    ):
        _snap.record_final_snapshot(
            run_id="run-fpg", module="manipola",
            node_id="node-fpg", node_type="result", df=df,
        )

    fake_get_storage.assert_not_called()
    assert captured_db["persistence_mode"] == "full_pg"
    assert captured_db["artifact_status"] == "none"
    assert captured_db["artifact_uri"] is None
    # full_pg: rows persistite (legacy)
    assert len(captured_db["rows_to_persist"]) == 3


def test_metadata_only_path_does_not_touch_object_storage():
    """Default produzione `metadata_only`: zero node_rows + zero
    artifact upload. Solo node_meta + node_hash."""
    from app.core.intermediate import snapshot as _snap

    df = pd.DataFrame({"a": list(range(100))})

    fake_get_storage = MagicMock(side_effect=AssertionError(
        "get_runtime_object_storage NON deve essere chiamato in metadata_only"
    ))
    captured_db = {}

    def _capture_db(**kwargs):
        captured_db.update(kwargs)

    with _patch_settings(mode="metadata_only"), patch(
        "app.core.io.object_storage.get_runtime_object_storage", fake_get_storage,
    ), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic",
        side_effect=_capture_db,
    ):
        _snap.record_final_snapshot(
            run_id="run-mo", module="manipola",
            node_id="node-mo", node_type="result", df=df,
        )

    fake_get_storage.assert_not_called()
    assert captured_db["persistence_mode"] == "metadata_only"
    assert captured_db["rows_to_persist"] == []
    assert captured_db["row_count"] == 100
    assert captured_db["artifact_status"] == "none"
    assert captured_db["artifact_uri"] is None
    assert captured_db["node_hash"]


# G. Status "failed" propagato a node_meta in caso di parquet error


def test_full_artifact_serialize_error_marks_failed_and_does_not_crash():
    """Se Parquet serialize fallisce (geometry non supportata,
    pyarrow assente, ecc.), `record_*_snapshot` non crasha; node_meta
    riceve `artifact_status="failed"` + `artifact_error` esplicito."""
    from app.core.intermediate import snapshot as _snap

    df = pd.DataFrame({"a": [1]})

    captured_db = {}

    def _capture_db(**kwargs):
        captured_db.update(kwargs)

    # Mock `_serialize_dataframe_to_parquet` per simulare fail.
    def _bad_serialize(_df):
        raise RuntimeError("pyarrow missing")

    uploads: dict = {}
    with _patch_settings(mode="full_artifact"), _mock_object_storage(uploads), \
            _patch_run_meta(), patch(
        "app.core.intermediate.artifact_writer._serialize_dataframe_to_parquet",
        side_effect=_bad_serialize,
    ), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic",
        side_effect=_capture_db,
    ):
        handle = _snap.record_final_snapshot(
            run_id="run-err", module="manipola",
            node_id="node-err", node_type="result", df=df,
        )

    # No crash, status failed, error message presente.
    assert handle.recorded is True
    assert handle.artifact_status == "failed"
    assert captured_db["artifact_status"] == "failed"
    assert captured_db["artifact_uri"] is None
    assert "pyarrow missing" in (captured_db["artifact_error"] or "")
    # Nessun upload sul fake storage (fallimento prima della upload_bytes)
    assert uploads == {}
