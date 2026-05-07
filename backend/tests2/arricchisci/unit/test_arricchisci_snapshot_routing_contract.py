"""Contract test Phase 6 Batch 3B — ARRICCHISCI snapshot routing.

Verifica:
  A. `_write_intermediate_snapshot` (sync helper) instrada via
     `record_checkpoint_snapshot(module="arricchisci")` invece di
     `write_node` legacy.
  B. Con policy `final_only` (default), la policy decide skip per i
     nodi `api_enrich`: niente df.to_dict, niente DB write.
  C. Quando un DataFrame e' empty/None, niente chiamata a snapshot.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _patch_settings(**overrides):
    from app.core.intermediate import policy as _policy
    from app.core.intermediate.policy import reset_validation_cache

    cfg = {
        "mode": "preview_only",
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
    cfg.update(overrides)
    reset_validation_cache()
    return patch.object(_policy, "_read_settings", return_value=cfg)


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    from app.core.intermediate.policy import reset_validation_cache
    reset_validation_cache()
    yield
    reset_validation_cache()


# Helpers


class _SupportTarget:
    """Bind del mixin `_write_intermediate_snapshot` a un oggetto vuoto
    per testarlo isolato dal resto di `ApiConnectionService`."""
    pass


def _bind_helper():
    from app.core.arricchisci.api_connection_support import (
        ApiConnectionSupportMixin,
    )
    target = _SupportTarget()
    target._write_intermediate_snapshot = (
        ApiConnectionSupportMixin._write_intermediate_snapshot.__get__(
            target, _SupportTarget,
        )
    )
    return target


# A. Helper sync routes through record_checkpoint_snapshot


def test_write_intermediate_snapshot_routes_through_record_checkpoint():
    """Phase 6 Batch 3B: il helper `_write_intermediate_snapshot` non
    chiama piu' write_node legacy, ma record_checkpoint_snapshot con
    module='arricchisci'."""
    target = _bind_helper()
    df = pd.DataFrame({"a": [1, 2, 3]})

    record_spy = MagicMock(return_value=MagicMock(recorded=False))
    write_node_spy = MagicMock(side_effect=AssertionError(
        "write_node legacy NON deve essere chiamato dall'helper"
    ))

    with patch(
        "app.core.intermediate.snapshot.record_checkpoint_snapshot", record_spy,
    ), patch(
        "app.core.intermediate.writer.write_node", write_node_spy,
    ), _patch_settings():
        target._write_intermediate_snapshot(
            run_id="run-1", node_id="ae-1", node_type="api_enrich", df=df,
        )

    record_spy.assert_called_once()
    kwargs = record_spy.call_args.kwargs
    assert kwargs["module"] == "arricchisci"
    assert kwargs["node_id"] == "ae-1"
    assert kwargs["node_type"] == "api_enrich"
    write_node_spy.assert_not_called()


def test_write_intermediate_snapshot_noop_on_empty_or_none():
    """No-op se il DataFrame e' None o vuoto: nessuna chiamata API,
    nessuna materializzazione."""
    target = _bind_helper()
    record_spy = MagicMock(side_effect=AssertionError(
        "Nessuna chiamata snapshot deve avvenire su df vuoto/None"
    ))

    with patch(
        "app.core.intermediate.snapshot.record_checkpoint_snapshot", record_spy,
    ), _patch_settings():
        target._write_intermediate_snapshot(
            run_id="run-1", node_id="ae-1", node_type="api_enrich", df=None,
        )
        target._write_intermediate_snapshot(
            run_id="run-1", node_id="ae-1", node_type="api_enrich",
            df=pd.DataFrame(),
        )

    record_spy.assert_not_called()


# B. Policy final_only -> nessun DB write per api_enrich


def test_api_enrich_with_final_only_policy_does_not_reach_db():
    """End-to-end: `_write_intermediate_snapshot` chiamata sotto policy
    `arricchisci_checkpoints=final_only` (default Batch 1) -> il resolver
    decide skip; il DB non viene mai scritto."""
    target = _bind_helper()
    df = pd.DataFrame({"a": [1, 2, 3]})

    db_spy = MagicMock(side_effect=AssertionError(
        "DB write NON deve avvenire per api_enrich con final_only"
    ))

    with patch(
        "app.core.intermediate.db.write_node_snapshot_atomic", db_spy,
    ), _patch_settings(arricchisci_checkpoints="final_only"):
        target._write_intermediate_snapshot(
            run_id="run-1", node_id="ae-1", node_type="api_enrich", df=df,
        )

    db_spy.assert_not_called()


def test_api_enrich_skip_path_does_not_materialize_dataframe():
    """Il DataFrame passato all'helper non deve subire `to_dict` ne'
    `head` quando la policy skip-pa."""
    target = _bind_helper()
    df = pd.DataFrame({"a": [1, 2, 3]})

    spy_df = MagicMock(wraps=df)
    spy_df.to_dict = MagicMock(side_effect=AssertionError(
        "to_dict NON deve essere chiamato in skip-path"
    ))
    spy_df.head = MagicMock(side_effect=AssertionError(
        "head NON deve essere chiamato in skip-path"
    ))
    # `not isinstance(df, pd.DataFrame)` early-return guard: spia DEVE
    # passare l'isinstance check. Usiamo wraps=df + un attributo empty.
    spy_df.empty = False

    # Il check `not isinstance(df, pd.DataFrame)` verra' valutato sul
    # MagicMock; iniettiamo `__class__ = pd.DataFrame` per superare.
    spy_df.__class__ = pd.DataFrame  # type: ignore[assignment]

    with _patch_settings(arricchisci_checkpoints="final_only"):
        # Il helper `_write_intermediate_snapshot` chiama
        # record_checkpoint_snapshot, che prima di toccare df chiama il
        # policy resolver. Se la policy dice skip, ritorna SnapshotHandle
        # senza fingerprint/preview. Niente to_dict, niente head.
        target._write_intermediate_snapshot(
            run_id="run-1", node_id="ae-1", node_type="api_enrich", df=spy_df,
        )

    # Le asserzioni sono dentro side_effect: se to_dict/head fossero
    # chiamati, AssertionError verrebbe sollevato.
    # Verifica difensiva esplicita.
    assert spy_df.to_dict.call_count == 0
    assert spy_df.head.call_count == 0
