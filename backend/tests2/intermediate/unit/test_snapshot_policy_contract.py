"""
Contract test Phase 6 Batch 1 — intermediate persistence policy.

Verifica:
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# A. Settings validation fail-fast


def _settings_baseline(**overrides) -> dict:
    base = {
        "mode": "preview_only",
        "preview_rows": 500,
        "artifact_format": "parquet",
        "manipola_checkpoints": "critical",
        "estrai_checkpoints": "final_only",
        "arricchisci_checkpoints": "final_only",
        "artifact_bucket": None,
        "artifact_prefix": "intermediate",
        # FIXME: #24: nuove settings fingerprint mode + sample rows
        "fingerprint_mode": "sample",
        "fingerprint_sample_rows": 1000,
    }
    base.update(overrides)
    return base


def test_validate_settings_baseline_ok():
    from app.core.intermediate.policy import validate_intermediate_settings
    validate_intermediate_settings(_settings_baseline())  # no raise


def test_validate_settings_rejects_full_artifact_when_object_storage_disabled():
    """Batch object storage intermedi (2026-04-28): `full_artifact` e'
    supportato ma richiede OBJECT_STORAGE_ENABLED=true. Senza, validate
    fail-fast con messaggio operativo."""
    from app.core.intermediate.policy import (
        IntermediateSettingsError, validate_intermediate_settings,
    )
    with patch(
        "app.shared.cloud_backends.object_storage_enabled",
        return_value=False,
    ):
        with pytest.raises(IntermediateSettingsError, match="OBJECT_STORAGE_ENABLED"):
            validate_intermediate_settings(_settings_baseline(mode="full_artifact"))


def test_validate_settings_accepts_full_artifact_when_object_storage_enabled():
    """Counterpart: con OBJECT_STORAGE_ENABLED=true, `full_artifact`
    passa la validazione."""
    from app.core.intermediate.policy import validate_intermediate_settings
    with patch(
        "app.shared.cloud_backends.object_storage_enabled",
        return_value=True,
    ):
        validate_intermediate_settings(_settings_baseline(mode="full_artifact"))


def test_validate_settings_rejects_unknown_mode():
    from app.core.intermediate.policy import (
        IntermediateSettingsError, validate_intermediate_settings,
    )
    with pytest.raises(IntermediateSettingsError, match="non riconosciuto"):
        validate_intermediate_settings(_settings_baseline(mode="lalala"))


def test_validate_settings_rejects_zero_or_negative_preview_rows():
    from app.core.intermediate.policy import (
        IntermediateSettingsError, validate_intermediate_settings,
    )
    with pytest.raises(IntermediateSettingsError, match="> 0"):
        validate_intermediate_settings(_settings_baseline(preview_rows=0))
    with pytest.raises(IntermediateSettingsError, match="> 0"):
        validate_intermediate_settings(_settings_baseline(preview_rows=-1))


def test_validate_settings_rejects_non_int_preview_rows():
    from app.core.intermediate.policy import (
        IntermediateSettingsError, validate_intermediate_settings,
    )
    with pytest.raises(IntermediateSettingsError, match="non e' un intero"):
        validate_intermediate_settings(_settings_baseline(preview_rows="abc"))


def test_validate_settings_rejects_unknown_manipola_checkpoints():
    from app.core.intermediate.policy import (
        IntermediateSettingsError, validate_intermediate_settings,
    )
    with pytest.raises(IntermediateSettingsError, match="MANIPOLA"):
        validate_intermediate_settings(
            _settings_baseline(manipola_checkpoints="banane")
        )


def test_validate_settings_rejects_estrai_other_than_final_only():
    from app.core.intermediate.policy import (
        IntermediateSettingsError, validate_intermediate_settings,
    )
    with pytest.raises(IntermediateSettingsError, match="ESTRAI"):
        validate_intermediate_settings(
            _settings_baseline(estrai_checkpoints="critical")
        )


def test_validate_settings_rejects_arricchisci_other_than_final_only():
    from app.core.intermediate.policy import (
        IntermediateSettingsError, validate_intermediate_settings,
    )
    with pytest.raises(IntermediateSettingsError, match="ARRICCHISCI"):
        validate_intermediate_settings(
            _settings_baseline(arricchisci_checkpoints="all")
        )


# B. ESTRAI / ARRICCHISCI checkpoint -> skip


def _patch_settings(**overrides):
    """Patcha `_read_settings` con override e resetta validation cache."""
    from app.core.intermediate import policy as _policy
    cfg = _settings_baseline(**overrides)
    return patch.object(_policy, "_read_settings", return_value=cfg)


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    from app.core.intermediate.policy import reset_validation_cache
    reset_validation_cache()
    yield
    reset_validation_cache()


def test_estrai_checkpoint_always_skipped():
    from app.core.intermediate.policy import resolve_intermediate_policy
    with _patch_settings():
        p = resolve_intermediate_policy(
            module="estrai", snapshot_kind="checkpoint", node_type="source",
        )
    assert p.recorded is False
    assert "estrai_checkpoints_disabled_in_batch1" in p.reason


def test_arricchisci_checkpoint_always_skipped():
    from app.core.intermediate.policy import resolve_intermediate_policy
    with _patch_settings():
        p = resolve_intermediate_policy(
            module="arricchisci",
            snapshot_kind="checkpoint",
            node_type="api_enrich",
        )
    assert p.recorded is False
    assert "arricchisci_checkpoints_disabled_in_batch1" in p.reason


# C. MANIPOLA checkpoint policy


def test_manipola_checkpoint_critical_join_recorded():
    from app.core.intermediate.policy import resolve_intermediate_policy
    with _patch_settings(manipola_checkpoints="critical"):
        p = resolve_intermediate_policy(
            module="manipola",
            snapshot_kind="checkpoint",
            node_type="transform",
            transform_id="join",
        )
    assert p.recorded is True
    assert p.reason == "manipola_critical"


def test_manipola_checkpoint_critical_skips_noncritical_transform():
    from app.core.intermediate.policy import resolve_intermediate_policy
    with _patch_settings(manipola_checkpoints="critical"):
        p = resolve_intermediate_policy(
            module="manipola",
            snapshot_kind="checkpoint",
            node_type="transform",
            transform_id="rename",
        )
    assert p.recorded is False
    assert p.reason == "manipola_skip_noncritical"


def test_manipola_checkpoint_none_skips_everything_except_final():
    from app.core.intermediate.policy import resolve_intermediate_policy
    with _patch_settings(manipola_checkpoints="none"):
        p = resolve_intermediate_policy(
            module="manipola",
            snapshot_kind="checkpoint",
            node_type="transform",
            transform_id="join",
        )
    assert p.recorded is False
    assert p.reason == "manipola_skip_none_policy"


def test_manipola_checkpoint_all_records_everything():
    from app.core.intermediate.policy import resolve_intermediate_policy
    with _patch_settings(manipola_checkpoints="all"):
        p = resolve_intermediate_policy(
            module="manipola",
            snapshot_kind="checkpoint",
            node_type="transform",
            transform_id="rename",
        )
    assert p.recorded is True
    assert p.reason == "manipola_all_policy"


def test_manipola_checkpoint_marked_only_records_save_intermediate_nodes():
    from app.core.intermediate.policy import resolve_intermediate_policy
    with _patch_settings(manipola_checkpoints="marked"):
        p = resolve_intermediate_policy(
            module="manipola",
            snapshot_kind="checkpoint",
            node_type="transform",
            transform_id="rename",
            node_data={"save_intermediate": True},
        )
    assert p.recorded is True
    assert p.reason == "manipola_marked_only"


def test_manipola_multi_join_always_critical():
    from app.core.intermediate.policy import resolve_intermediate_policy
    with _patch_settings(manipola_checkpoints="critical"):
        p = resolve_intermediate_policy(
            module="manipola",
            snapshot_kind="checkpoint",
            node_type="multi_join",
        )
    assert p.recorded is True
    assert p.reason == "manipola_multi_join"


# D. Final sempre registrato (mode globale decide row write)


def test_estrai_final_recorded_with_mode_preview_only():
    from app.core.intermediate.policy import resolve_intermediate_policy
    with _patch_settings(mode="preview_only"):
        p = resolve_intermediate_policy(
            module="estrai", snapshot_kind="final", node_type="result",
        )
    assert p.recorded is True
    assert p.persistence_mode == "preview_only"
    assert p.preview_rows == 500
    assert p.should_record_rows is True


def test_manipola_final_recorded_with_mode_metadata_only():
    from app.core.intermediate.policy import resolve_intermediate_policy
    with _patch_settings(mode="metadata_only"):
        p = resolve_intermediate_policy(
            module="manipola", snapshot_kind="final", node_type="result",
        )
    assert p.recorded is True
    assert p.persistence_mode == "metadata_only"
    assert p.should_record_rows is False
    assert p.preview_rows == 0


def test_arricchisci_final_recorded_with_mode_full_pg():
    from app.core.intermediate.policy import resolve_intermediate_policy
    with _patch_settings(mode="full_pg"):
        p = resolve_intermediate_policy(
            module="arricchisci", snapshot_kind="final", node_type="result",
        )
    assert p.recorded is True
    assert p.persistence_mode == "full_pg"
    assert p.should_record_rows is True


# E. record_checkpoint_snapshot skip path: no work


def _make_spied_df(nrows: int = 1000, ncols: int = 3) -> MagicMock:
    """Costruisce un DataFrame reale e spia .to_dict per verificare
    che NON venga chiamato durante uno skip."""
    df = pd.DataFrame({
        f"c{i}": list(range(nrows)) for i in range(ncols)
    })
    spy = MagicMock(wraps=df)
    spy.to_dict = MagicMock(side_effect=AssertionError(
        "to_dict NON deve essere chiamato durante skip"
    ))
    # Per essere indistinguibile dal vero DataFrame:
    spy.__class__ = pd.DataFrame
    return spy, df


def test_checkpoint_skip_does_not_touch_dataframe_or_db():
    """Quando la policy decide skip, NON deve:
       - chiamare df.to_dict
       - calcolare hash
       - toccare il DB
    Verifica via mocks su tutti i sink."""
    from app.core.intermediate import snapshot as _snap

    df = pd.DataFrame({"c0": list(range(10_000)), "c1": list(range(10_000))})
    df_spy = MagicMock(wraps=df)
    df_spy.to_dict = MagicMock(side_effect=AssertionError(
        "to_dict NON deve essere chiamato durante skip"
    ))
    # head() non deve essere chiamato neanche
    df_spy.head = MagicMock(side_effect=AssertionError(
        "head NON deve essere chiamato durante skip"
    ))

    fingerprint_spy = MagicMock(side_effect=AssertionError(
        "fingerprint NON deve essere chiamato durante skip"
    ))
    db_spy = MagicMock(side_effect=AssertionError(
        "write_node_snapshot_atomic NON deve essere chiamato durante skip"
    ))

    with _patch_settings(manipola_checkpoints="none"), patch.object(
        _snap, "compute_dataframe_fingerprint", fingerprint_spy,
    ), patch(
        "app.core.intermediate.db.write_node_snapshot_atomic", db_spy,
    ):
        handle = _snap.record_checkpoint_snapshot(
            run_id="run-1",
            module="manipola",
            node_id="n1",
            node_type="transform",
            df=df_spy,
            transform_id="join",
        )

    assert handle.recorded is False
    assert handle.reason == "manipola_skip_none_policy"
    fingerprint_spy.assert_not_called()
    db_spy.assert_not_called()


# F. compute_dataframe_fingerprint


def test_fingerprint_deterministic_on_identical_dataframe():
    """Bug #24: API ora ritorna `(hex_digest, hash_algo)`. Determinismo
    invariato."""
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    with _patch_settings():
        h1, algo1 = compute_dataframe_fingerprint(df.copy())
        h2, algo2 = compute_dataframe_fingerprint(df.copy())
    assert h1 == h2
    assert algo1 == algo2
    assert len(h1) == 64  # sha256 hex


def test_fingerprint_changes_when_data_changes():
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df_a = pd.DataFrame({"a": [1, 2, 3]})
    df_b = pd.DataFrame({"a": [1, 2, 4]})
    with _patch_settings():
        assert compute_dataframe_fingerprint(df_a)[0] != compute_dataframe_fingerprint(df_b)[0]


def test_fingerprint_changes_when_schema_changes():
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df_a = pd.DataFrame({"a": [1, 2, 3]})
    df_b = pd.DataFrame({"b": [1, 2, 3]})
    with _patch_settings():
        assert compute_dataframe_fingerprint(df_a)[0] != compute_dataframe_fingerprint(df_b)[0]


def test_fingerprint_none_dataframe():
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    with _patch_settings():
        h, algo = compute_dataframe_fingerprint(None)
    assert len(h) == 64
    # Default mode = sample -> algo riflette la mode richiesta anche
    # quando il DataFrame e' None (deterministico).
    assert algo == "pandas_sample_hash_v1"


# G. record_final_snapshot end-to-end (DB mocked)


def _capture_db_call():
    """Patcha write_node_snapshot_atomic per catturare i kwargs."""
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)

    return captured, _capture


def test_record_final_metadata_only_zero_rows_real_count_hash_present():
    from app.core.intermediate import snapshot as _snap

    df = pd.DataFrame({"a": list(range(2000)), "b": ["x"] * 2000})
    captured, capture = _capture_db_call()

    with _patch_settings(mode="metadata_only"), patch(
        "app.core.intermediate.db.write_node_snapshot_atomic",
        side_effect=capture,
    ):
        handle = _snap.record_final_snapshot(
            run_id="r1", module="manipola", node_id="result",
            node_type="result", df=df,
        )

    assert handle.recorded is True
    assert handle.persistence_mode == "metadata_only"
    assert handle.row_count == 2000
    assert handle.persisted_rows_count == 0
    assert handle.preview_truncated is False  # in metadata_only non c'e' preview
    assert handle.node_hash and len(handle.node_hash) == 64
    # FIXME: #24: default fingerprint mode = "sample" -> hash_algo
    # = pandas_sample_hash_v1. Audit forte richiede mode="full".
    assert handle.hash_algo == "pandas_sample_hash_v1"

    # DB call: rows_to_persist vuoto, row_count reale, node_hash presente.
    assert captured["row_count"] == 2000
    assert captured["rows_to_persist"] == []
    assert captured["node_hash"] == handle.node_hash
    assert captured["persistence_mode"] == "metadata_only"
    assert captured["artifact_status"] == "none"


def test_record_final_preview_only_caps_rows_real_count_truncated_flag():
    from app.core.intermediate import snapshot as _snap

    df = pd.DataFrame({"a": list(range(2000)), "b": list(range(2000))})
    captured, capture = _capture_db_call()

    with _patch_settings(mode="preview_only", preview_rows=500), patch(
        "app.core.intermediate.db.write_node_snapshot_atomic",
        side_effect=capture,
    ):
        handle = _snap.record_final_snapshot(
            run_id="r2", module="manipola", node_id="result",
            node_type="result", df=df,
        )

    assert handle.recorded is True
    assert handle.persistence_mode == "preview_only"
    assert handle.row_count == 2000
    assert handle.persisted_rows_count == 500
    assert handle.preview_truncated is True
    assert handle.node_hash and len(handle.node_hash) == 64

    assert captured["row_count"] == 2000
    assert len(captured["rows_to_persist"]) == 500
    # Ogni preview row ha _row_hash
    assert all("_row_hash" in r for r in captured["rows_to_persist"])
    assert captured["preview_truncated"] is True
    assert captured["persistence_mode"] == "preview_only"


def test_record_final_preview_only_no_truncation_when_dataset_smaller_than_cap():
    from app.core.intermediate import snapshot as _snap

    df = pd.DataFrame({"a": [1, 2, 3]})
    captured, capture = _capture_db_call()

    with _patch_settings(mode="preview_only", preview_rows=500), patch(
        "app.core.intermediate.db.write_node_snapshot_atomic",
        side_effect=capture,
    ):
        handle = _snap.record_final_snapshot(
            run_id="r3", module="estrai", node_id="result",
            node_type="result", df=df,
        )

    assert handle.persisted_rows_count == 3
    assert handle.preview_truncated is False
    assert captured["preview_truncated"] is False


def test_record_final_full_pg_persists_all_rows():
    from app.core.intermediate import snapshot as _snap

    df = pd.DataFrame({"a": list(range(50)), "b": list(range(50))})
    captured, capture = _capture_db_call()

    with _patch_settings(mode="full_pg"), patch(
        "app.core.intermediate.db.write_node_snapshot_atomic",
        side_effect=capture,
    ):
        handle = _snap.record_final_snapshot(
            run_id="r4", module="manipola", node_id="result",
            node_type="result", df=df,
        )

    assert handle.persistence_mode == "full_pg"
    assert handle.row_count == 50
    assert handle.persisted_rows_count == 50
    assert handle.preview_truncated is False
    assert len(captured["rows_to_persist"]) == 50


# Hash invariante fra mode (sullo stesso DataFrame)


def test_node_hash_invariant_across_modes_on_same_dataframe():
    """Lo stesso DataFrame, registrato con MODE diversi, produce sempre
    lo stesso `node_hash`. La policy gestisce le righe persistite, NON
    il fingerprint."""
    from app.core.intermediate import snapshot as _snap

    df = pd.DataFrame({"a": list(range(1000)), "b": ["x"] * 1000})

    with _patch_settings(mode="metadata_only"), patch(
        "app.core.intermediate.db.write_node_snapshot_atomic",
    ):
        h_meta = _snap.record_final_snapshot(
            run_id="rA", module="manipola", node_id="r",
            node_type="result", df=df,
        ).node_hash

    with _patch_settings(mode="preview_only", preview_rows=500), patch(
        "app.core.intermediate.db.write_node_snapshot_atomic",
    ):
        h_prev = _snap.record_final_snapshot(
            run_id="rB", module="manipola", node_id="r",
            node_type="result", df=df,
        ).node_hash

    with _patch_settings(mode="full_pg"), patch(
        "app.core.intermediate.db.write_node_snapshot_atomic",
    ):
        h_full = _snap.record_final_snapshot(
            run_id="rC", module="manipola", node_id="r",
            node_type="result", df=df,
        ).node_hash

    assert h_meta == h_prev == h_full


# H. Legacy NULL records -> full_pg


def test_persistence_mode_for_record_null_returns_full_pg():
    from app.core.intermediate.policy import persistence_mode_for_record
    assert persistence_mode_for_record({"persistence_mode": None}) == "full_pg"
    assert persistence_mode_for_record({}) == "full_pg"
    assert persistence_mode_for_record({"persistence_mode": ""}) == "full_pg"


def test_persistence_mode_for_record_passes_through_known_value():
    from app.core.intermediate.policy import persistence_mode_for_record
    assert persistence_mode_for_record(
        {"persistence_mode": "preview_only"}
    ) == "preview_only"
    assert persistence_mode_for_record(
        {"persistence_mode": "metadata_only"}
    ) == "metadata_only"


# I. flush_snapshots stub (Batch 1)


def test_flush_snapshots_stub_returns_zero_pending():
    from app.core.intermediate.snapshot import flush_snapshots
    res = flush_snapshots("run-x")
    assert res.run_id == "run-x"
    assert res.flushed == 0
    assert res.pending == 0
    assert res.errors == []
