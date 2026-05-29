"""Contract test RB.#14 — parallel-merge fail-hard guard.

Dopo l'unificazione del loop (B step C3b) il merge dei batch paralleli e'
`app.core.dataframes.parallel_ops.validate_and_merge_parallel_columns` (usato da
`execute_parallel_enrichers`), non piu' il rimosso
`_merge_batch_parallel_node_results` del task. L'invariante resta: merge di
colonne disgiunte + fail-hard su mismatch index/cardinalita' (evita la silent
corruption riga<->valore del merge positional).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.dataframes.parallel_ops import validate_and_merge_parallel_columns


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_merges_disjoint_columns_from_parallel_nodes():
    """Le colonne nuove di ogni nodo del batch confluiscono nel frame comune."""
    input_df = pd.DataFrame({"seed": [1, 2, 3]})
    node_results = {
        "n1": pd.DataFrame({"seed": [1, 2, 3], "a": [10, 20, 30]}),
        "n2": pd.DataFrame({"seed": [1, 2, 3], "b": ["x", "y", "z"]}),
    }
    merged = validate_and_merge_parallel_columns(input_df, ["n1", "n2"], node_results)
    assert list(merged.columns) == ["seed", "a", "b"]
    assert merged["a"].tolist() == [10, 20, 30]
    assert merged["b"].tolist() == ["x", "y", "z"]


def test_fail_hard_on_cardinality_mismatch():
    """Un nodo che cambia cardinalita' (dedup/esplosione) deve far raise:
    il merge positional .values produrrebbe corruption riga<->valore."""
    input_df = pd.DataFrame({"seed": [1, 2, 3]})
    node_results = {"n1": pd.DataFrame({"seed": [1, 2], "a": [10, 20]})}
    with pytest.raises(RuntimeError, match="mismatch index"):
        validate_and_merge_parallel_columns(input_df, ["n1"], node_results)


def test_non_dataframe_results_are_skipped():
    """Risultati non-DataFrame (None / dict marker) sono ignorati nel merge."""
    input_df = pd.DataFrame({"seed": [1, 2]})
    node_results = {"n1": None, "n2": {"_passthrough_file_source": True}}
    merged = validate_and_merge_parallel_columns(input_df, ["n1", "n2"], node_results)
    assert list(merged.columns) == ["seed"]
