"""
Contract test RB.#15 (2026-04-28) — detector cardinality-changing nodes

e isolamento dai batch paralleli.
"""

from __future__ import annotations

import pytest

from app.core.arricchisci.parallel import (
    _node_may_explode_cardinality,
    compute_parallel_groups,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# 1-7. Detector unit


def test_detector_path_with_brackets_returns_true():
    """`features[].x` -> True (esplosione esplicita path A multi-row)."""
    node = {
        "id": "n1",
        "type": "api_enrich",
        "output_mapping": [{"path": "features[].x", "column": "x"}],
    }
    assert _node_may_explode_cardinality(node) is True


def test_detector_path_with_star_returns_true():
    """`features[*].x` -> True (la sintassi `[*]` viene normalizzata a
    `[]` da `_extract_output`, semanticamente equivalente)."""
    node = {
        "id": "n1",
        "type": "api_enrich",
        "output_mapping": [{"path": "features[*].x", "column": "x"}],
    }
    assert _node_may_explode_cardinality(node) is True


def test_detector_simple_path_returns_false():
    """Path scalari senza `[]`/`[*]` -> False (cardinality-preserving)."""
    node = {
        "id": "n1",
        "type": "api_enrich",
        "output_mapping": [
            {"path": "data.x", "column": "x"},
            {"path": "result.value", "column": "v"},
        ],
    }
    assert _node_may_explode_cardinality(node) is False


def test_detector_empty_mapping_returns_false():
    """`output_mapping=[]` -> False (path B `_auto_flatten` non rilevabile
    staticamente; falso negativo accettato, rete fail-hard RB.#14)."""
    node = {"id": "n1", "type": "api_enrich", "output_mapping": []}
    assert _node_may_explode_cardinality(node) is False


def test_detector_reads_output_mapping_from_node_data_dict():
    """Frontend canvas pattern: `output_mapping` dentro `node.data`."""
    node = {
        "id": "n1",
        "type": "api_enrich",
        "data": {
            "output_mapping": [{"path": "features[*].x", "column": "x"}],
        },
    }
    assert _node_may_explode_cardinality(node) is True


def test_detector_falls_back_to_api_config_output_mapping():
    """Caso reale `Returnomifrompoint`: `node.output_mapping` vuoto, ma
    `api_connections[api_id].output_mapping` ha multi-row paths.
    Detector ritorna True dal lookup centralized."""
    node = {
        "id": "n1",
        "type": "api_enrich",
        "api_id": "api_2cab32fcfb16",
        # node-level output_mapping vuoto (config centralizzato)
    }
    api_connections = {
        "api_2cab32fcfb16": {
            "name": "Returnomifrompoint",
            "output_mapping": [
                {"path": "features[*].properties.nome", "column": "nome"},
                {"path": "features[*].properties.valori[*].anno", "column": "anno"},
            ],
        }
    }
    assert _node_may_explode_cardinality(node, api_connections) is True


def test_detector_non_api_enrich_returns_false():
    """Tipi nodo non api_enrich (source, file_source, result, ecc.) ->
    False, indipendentemente da eventuali output_mapping."""
    for ntype in ("source", "result", "file_source", "project_source", "staticValues"):
        node = {
            "id": "n1",
            "type": ntype,
            "output_mapping": [{"path": "features[*].x", "column": "x"}],
        }
        assert _node_may_explode_cardinality(node) is False, (
            f"Atteso False per type={ntype}"
        )


# 8-11. compute_parallel_groups integration


def _make_api_enrich_node(node_id: str, output_mapping: list, api_id: str = None) -> dict:
    n = {
        "id": node_id,
        "type": "api_enrich",
        "output_mapping": output_mapping,
        "param_mapping": {},
    }
    if api_id:
        n["api_id"] = api_id
    return n


def test_compute_groups_isolates_cardinality_changing_node():
    """DAG: 2 cardinality-preserving + 1 cardinality-changing intercalato.
    Atteso: il batch viene flushato prima del C-C, il C-C come single,
    poi un nuovo batch parallel ricominciabile per i preserving successivi."""
    nodes = [
        _make_api_enrich_node("p1", [{"path": "data.a", "column": "a"}]),
        _make_api_enrich_node("c1", [{"path": "features[*].x", "column": "x"}]),
        _make_api_enrich_node("p2", [{"path": "data.b", "column": "b"}]),
    ]
    execution_order = ["p1", "c1", "p2"]

    groups = compute_parallel_groups(execution_order, nodes, edges=[])

    # Atteso: [(parallel, [p1]), (single, [c1]), (parallel, [p2])]
    assert len(groups) == 3
    assert groups[0] == ("parallel", ["p1"])
    assert groups[1] == ("single", ["c1"])
    assert groups[2] == ("parallel", ["p2"])


def test_compute_groups_multiple_cardinality_changing_become_separate_singletons():
    """N nodi cardinality-changing fratelli -> N single separati. Eseguiti
    sequenzialmente uno dopo l'altro, ognuno propaga la sua cardinality
    al successivo (limitazione: solo se la topology DAG li mette
    upstream/downstream; se sono tutti fratelli con stesso input,
    l'esplosione del primo non si propaga ai successivi - documentato
    nel docstring del detector)."""
    nodes = [
        _make_api_enrich_node("c1", [{"path": "features[*].x", "column": "x"}]),
        _make_api_enrich_node("c2", [{"path": "results[*].v", "column": "v"}]),
    ]
    groups = compute_parallel_groups(["c1", "c2"], nodes, edges=[])

    assert groups == [("single", ["c1"]), ("single", ["c2"])]


def test_compute_groups_only_cardinality_changing_emits_single():
    """1 nodo C-C solo -> [single([1])]."""
    nodes = [_make_api_enrich_node("c1", [{"path": "features[*].x", "column": "x"}])]
    groups = compute_parallel_groups(["c1"], nodes, edges=[])
    assert groups == [("single", ["c1"])]


def test_compute_groups_all_preserving_unchanged_regression():
    """Regression RB.#14 / pre-RB.#15: 11 cardinality-preserving fratelli
    in un unico batch parallel. Comportamento invariato dal fix RB.#15."""
    nodes = [
        _make_api_enrich_node(f"p{i}", [{"path": f"data.col_{i}", "column": f"c{i}"}])
        for i in range(11)
    ]
    execution_order = [f"p{i}" for i in range(11)]
    groups = compute_parallel_groups(execution_order, nodes, edges=[])

    # Tutti in un unico batch parallel
    assert len(groups) == 1
    assert groups[0][0] == "parallel"
    assert sorted(groups[0][1]) == sorted([f"p{i}" for i in range(11)])


def test_compute_groups_real_bug_15_topology():
    """Topology reale BUG.md #15: 1 predecessore (XeY) + 11 fratelli
    (Returnomifrompoint cardinality-changing + 10 Geosafe preserving).

    NB: nel caso reale XeY ha output_mapping con `[*]` ma di fatto produce
    cardinality 1:1. Il detector lo classifica conservativamente come
    may_explode -> single. Trade-off accettato (XeY era gia' single
    nel DAG osservato come predecessore unico). Test riflette il
    comportamento conservativo: 12 single + 1 batch da 10."""
    nodes = (
        # XeY-style (output con `[*]` ma in pratica 1:1)
        [_make_api_enrich_node("xey", [
            {"path": "features[*].geometry.coordinates[0]", "column": "X"},
            {"path": "features[*].geometry.coordinates[1]", "column": "Y"},
        ])]
        # Returnomifrompoint (cardinality-changing reale)
        + [_make_api_enrich_node("returnomi", [
            {"path": "features[*].properties.nome", "column": "nome"},
            {"path": "features[*].properties.valori[*].anno", "column": "anno"},
        ])]
        # 10 Geosafe (cardinality-preserving)
        + [
            _make_api_enrich_node(f"geosafe_{i}", [
                {"path": f"results.score_{i}", "column": f"score_{i}"},
            ])
            for i in range(10)
        ]
    )
    execution_order = (
        ["xey", "returnomi"]
        + [f"geosafe_{i}" for i in range(10)]
    )
    groups = compute_parallel_groups(execution_order, nodes, edges=[])

    # XeY -> single (false positive may_explode benigno)
    # Returnomi -> single (true positive)
    # 10 Geosafe -> 1 batch parallel
    assert groups[0] == ("single", ["xey"])
    assert groups[1] == ("single", ["returnomi"])
    assert groups[2][0] == "parallel"
    assert sorted(groups[2][1]) == sorted([f"geosafe_{i}" for i in range(10)])


# 12. Anti-regression: fail-hard RB.#14 resta attivo per falsi negativi


def test_fail_hard_safety_net_still_documented_for_path_b_explosion():
    """Anti-regression: il detector RB.#15 NON copre path B (output_mapping
    vuoto + response runtime FeatureCollection via `_auto_flatten`).
    In quel caso il nodo finisce in un batch parallel con altri, e se
    runtime esplode -> `validate_and_merge_parallel_columns` raise
    RuntimeError (fail-hard RB.#14). Questo test documenta che il
    fail-hard NON e' stato rimosso/indebolito da RB.#15.

    Verifica strutturale: il merge guard esiste nel dataframe engine
    (post B step C3b il task delega il parallelo a
    `execute_parallel_enrichers`, che usa questo merge)."""
    from app.core.dataframes.parallel_ops import validate_and_merge_parallel_columns

    # Helper presente, importabile, callable. Test di RB.#14 in
    # tests2/arricchisci/unit/test_batch_parallel_merge_contract.py
    # blindano il behavior su mismatch cardinalita'.
    assert callable(validate_and_merge_parallel_columns)


def test_detector_handles_null_or_missing_paths_gracefully():
    """Robustezza: output_mapping con dict che non hanno `path` o
    `path=None` -> non esplode, ritorna False per quei mapping."""
    node = {
        "id": "n1",
        "type": "api_enrich",
        "output_mapping": [
            {"column": "x"},  # no path
            {"path": None, "column": "y"},
            {"path": "", "column": "z"},
            {"path": "data.simple", "column": "w"},
        ],
    }
    assert _node_may_explode_cardinality(node) is False
