"""
Contract test P1-06 / F-DATA-07 — guard index/cardinalita' su parallel merge.

Scope:
"""

from __future__ import annotations


import pandas as pd
import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
    pytest.mark.regression,
]


# Helpers

def _make_input_df() -> pd.DataFrame:
    """3 righe con index default (RangeIndex 0..2)."""
    return pd.DataFrame({"id": [1, 2, 3], "city": ["Roma", "Milano", "Napoli"]})


def _identity_executor(node, api_connections, df: pd.DataFrame) -> pd.DataFrame:
    """Aggiunge una colonna preservando index/order."""
    out = df.copy()
    out["enriched"] = ["A", "B", "C"][: len(df)]
    return out


def _drops_one_row_executor(node, api_connections, df: pd.DataFrame) -> pd.DataFrame:
    """Restituisce N-1 righe (silent dedup): cardinalita' divergente."""
    out = df.iloc[:-1].copy()
    out["enriched"] = ["A", "B"][: len(out)]
    return out


def _resets_index_executor(node, api_connections, df: pd.DataFrame) -> pd.DataFrame:
    """Stessa cardinalita' ma index resettato (RangeIndex 0..N-1).

    Path legittimo se anche l'input ha RangeIndex 0..N-1 (positional
    alignment ben definito). Coperto dal fallback `same_len + both_default_range`.
    """
    out = df.copy().reset_index(drop=True)
    out["enriched"] = ["A", "B", "C"][: len(out)]
    return out


def _permutes_index_executor(node, api_connections, df: pd.DataFrame) -> pd.DataFrame:
    """Stessa cardinalita' ma index non-Range permutato: positional align
    associerebbe valori alle righe sbagliate."""
    out = df.copy()
    # Index custom non-RangeIndex permutato -> guard deve rifiutare.
    out.index = pd.Index([10, 20, 30])
    out["enriched"] = ["A", "B", "C"]
    return out


# Tests

def test_merge_succeeds_when_index_matches():
    """Happy path: executor preserva index -> merge ok, valori allineati."""
    from app.core.arricchisci.parallel import execute_parallel_enrichers

    input_df = _make_input_df()
    node_map = {"n1": {"id": "n1", "data": {"api_connection_id": "api_x"}}}

    results, err = execute_parallel_enrichers(
        node_ids=["n1"],
        input_df=input_df,
        node_map=node_map,
        api_connections={"api_x": {}},
        execute_single_fn=_identity_executor,
    )

    assert err is None
    merged = results["n1"]
    assert list(merged["enriched"]) == ["A", "B", "C"]
    assert list(merged["id"]) == [1, 2, 3]
    assert merged.index.equals(input_df.index)


def test_merge_raises_when_cardinality_mismatch():
    """Executor che dedupplica -> RuntimeError diagnostico, no silent corruption."""
    from app.core.arricchisci.parallel import execute_parallel_enrichers

    input_df = _make_input_df()
    node_map = {"n1": {"id": "n1", "data": {"api_connection_id": "api_x"}}}

    with pytest.raises(RuntimeError) as exc:
        execute_parallel_enrichers(
            node_ids=["n1"],
            input_df=input_df,
            node_map=node_map,
            api_connections={"api_x": {}},
            execute_single_fn=_drops_one_row_executor,
        )
    msg = str(exc.value)
    assert "[parallel-merge]" in msg
    assert "n1" in msg, "Il messaggio deve identificare il nodo problematico"
    assert "2" in msg and "3" in msg, "Cardinalita' attese e trovate nel messaggio"


def test_merge_raises_when_index_order_diverges():
    """Stessa cardinalita' ma index non-Range permutato -> RuntimeError.

    Senza guard, positional `.values` produrrebbe enriched allineato sui
    valori dell'input ma associato a righe con index diverso, semantica
    sbagliata se downstream usa l'index per identificare le righe.
    """
    from app.core.arricchisci.parallel import execute_parallel_enrichers

    input_df = _make_input_df()
    node_map = {"n1": {"id": "n1", "data": {"api_connection_id": "api_x"}}}

    with pytest.raises(RuntimeError) as exc:
        execute_parallel_enrichers(
            node_ids=["n1"],
            input_df=input_df,
            node_map=node_map,
            api_connections={"api_x": {}},
            execute_single_fn=_permutes_index_executor,
        )
    assert "[parallel-merge]" in str(exc.value)


def test_merge_succeeds_when_both_range_index_after_reset():
    """RangeIndex default equivalenti (post-reset_index su input gia'
    RangeIndex default 0..N-1): il fallback `same_len + both_default_range`
    consente il merge senza errore."""
    from app.core.arricchisci.parallel import execute_parallel_enrichers

    input_df = _make_input_df()  # gia' RangeIndex 0..2
    node_map = {"n1": {"id": "n1", "data": {"api_connection_id": "api_x"}}}

    results, err = execute_parallel_enrichers(
        node_ids=["n1"],
        input_df=input_df,
        node_map=node_map,
        api_connections={"api_x": {}},
        execute_single_fn=_resets_index_executor,
    )

    assert err is None
    assert list(results["n1"]["enriched"]) == ["A", "B", "C"]


def test_merge_raises_when_input_range_is_non_default_and_executor_resets():
    """Input ha RangeIndex non-default (es. RangeIndex(10, 13)) e l'executor
    fa `reset_index(drop=True)`: l'identita' dell'index originale e' persa,
    positional merge ambiguo -> RuntimeError.

    Contesto contratto: il fallback PRE-review accettava `same_len and
    both_range` (qualsiasi RangeIndex con stessa lunghezza). Quel
    contratto era troppo permissivo e lasciava passare casi come
    merged.index=RangeIndex(10,13) + result.index=RangeIndex(0,3).
    Il contratto ATTUALE e' `same_len and both_default_range` (entrambi
    devono essere il RangeIndex default `RangeIndex(0, N)`); questo test
    fissa la regressione affinche' il fallback non torni indietro.
    """
    from app.core.arricchisci.parallel import execute_parallel_enrichers

    input_df = pd.DataFrame(
        {"id": [1, 2, 3], "city": ["Roma", "Milano", "Napoli"]},
        index=pd.RangeIndex(10, 13),
    )
    node_map = {"n1": {"id": "n1", "data": {"api_connection_id": "api_x"}}}

    with pytest.raises(RuntimeError) as exc:
        execute_parallel_enrichers(
            node_ids=["n1"],
            input_df=input_df,
            node_map=node_map,
            api_connections={"api_x": {}},
            execute_single_fn=_resets_index_executor,
        )
    msg = str(exc.value)
    assert "[parallel-merge]" in msg
    assert "n1" in msg


def test_merge_split_execute_if_path_unaffected(monkeypatch):
    """Branch `all_exec=False` (split per `execute_if`): pre-fix il codice
    fa `pd.concat([result_exec, df_skip]).sort_index()`. La guard deve
    accettare l'output perche' index coincide con input dopo sort.
    """
    from app.core.arricchisci import parallel as parallel_mod

    input_df = pd.DataFrame({
        "id": [1, 2, 3, 4],
        "city": ["Roma", "Milano", "Napoli", "Torino"],
        "do_enrich": [True, False, True, False],
    })

    # Eseguibile solo sulle righe con do_enrich=True; le altre passthrough.
    def _selective_executor(node, api_connections, df):
        out = df.copy()
        out["enriched"] = [f"E_{i}" for i in df["id"].tolist()]
        return out

    # Forziamo split via monkeypatch di get_execute_mask (auto-undo a teardown).
    def _fake_mask(node, df):
        return df["do_enrich"]

    monkeypatch.setattr(parallel_mod, "get_execute_mask", _fake_mask)

    node_map = {
        "n1": {
            "id": "n1",
            "data": {"api_connection_id": "api_x", "execute_if": "do_enrich==true"},
        }
    }
    results, err = parallel_mod.execute_parallel_enrichers(
        node_ids=["n1"],
        input_df=input_df,
        node_map=node_map,
        api_connections={"api_x": {}},
        execute_single_fn=_selective_executor,
    )

    assert err is None
    merged = results["n1"]
    assert len(merged) == 4, "Cardinalita' input preservata anche con split"
    assert merged.index.equals(input_df.index), "Index ricomposto via sort_index"
    # Le righe split-execute hanno enriched valorizzato; le passthrough None.
    assert merged.loc[0, "enriched"] == "E_1"
    assert merged.loc[2, "enriched"] == "E_3"
    assert pd.isna(merged.loc[1, "enriched"])
    assert pd.isna(merged.loc[3, "enriched"])
