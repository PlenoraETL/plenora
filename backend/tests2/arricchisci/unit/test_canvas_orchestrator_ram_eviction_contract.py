"""Contract #37 — eviction RAM dei nodi intermedi in run_enrichment_canvas.

Reference-counting topologico: appena tutti i consumatori di un nodo hanno
girato, il suo DataFrame viene liberato da `node_results`. I nodi terminali
(out-degree 0, es. `result`) NON vengono mai evictati (li legge il caller via
`_find_final_result`).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.arricchisci.canvas_orchestrator import run_enrichment_canvas


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


class _NodeView:
    def __init__(self, node_type: str, data: dict):
        self.type = node_type
        self._data = data

    def get_field(self, key, default=None):
        return self._data.get(key, default)


class _FakeGraph:
    """Graph duck-typed minimale per run_enrichment_canvas."""

    def __init__(self, nodes: dict, edges: list):
        # nodes: {id: (type, data_dict)}
        self._types = {nid: t for nid, (t, _d) in nodes.items()}
        self.raw_map = {nid: d for nid, (_t, d) in nodes.items()}
        self.raw_nodes = list(self.raw_map.values())
        self.edges = edges

    def raw(self, nid):
        return self.raw_map.get(nid)

    def get(self, nid):
        return _NodeView(self._types[nid], self.raw_map[nid])


class _FakeSvc:
    def _load_static_values(self, node_data, ctx=None):
        return pd.DataFrame({"v": [1, 2, 3]})


def test_intermediate_nodes_evicted_terminal_kept():
    # source(staticValues) -> enrich(api_enrich) -> result
    nodes = {
        "src": ("staticValues", {"id": "src"}),
        "enr": ("api_enrich", {"id": "enr"}),
        "res": ("result", {"id": "res"}),
    }
    edges = [
        {"source": "src", "target": "enr"},
        {"source": "enr", "target": "res"},
    ]
    graph = _FakeGraph(nodes, edges)
    groups = [("single", ["src"]), ("single", ["enr"]), ("single", ["res"])]

    def _enrich_fn(node_data, api_conns, in_df):
        # arricchimento finto: aggiunge una colonna
        out = in_df.copy()
        out["enriched"] = out["v"] * 10
        return out

    node_results = run_enrichment_canvas(
        _FakeSvc(),
        "proj-1",
        graph=graph,
        ctx=object(),
        api_connections={},
        execution_order=["src", "enr", "res"],
        groups=groups,
        enrich_fn=_enrich_fn,
    )

    # Terminale presente (il caller lo legge)
    assert "res" in node_results
    assert list(node_results["res"]["enriched"]) == [10, 20, 30]
    # Intermedi liberati dalla RAM (consumatori esauriti)
    assert "src" not in node_results, "il source doveva essere evictato dopo enr"
    assert "enr" not in node_results, "l'enricher doveva essere evictato dopo result"


def test_fan_out_node_evicted_only_after_all_consumers():
    # src consumato da DUE nodi (a, b); deve sopravvivere finche' entrambi non girano
    nodes = {
        "src": ("staticValues", {"id": "src"}),
        "a": ("api_enrich", {"id": "a"}),
        "b": ("api_enrich", {"id": "b"}),
    }
    edges = [
        {"source": "src", "target": "a"},
        {"source": "src", "target": "b"},
    ]
    graph = _FakeGraph(nodes, edges)
    # esecuzione: src, poi a, poi b. Dopo 'a' src ha ancora 'b' come consumatore.
    groups = [("single", ["src"]), ("single", ["a"]), ("single", ["b"])]

    def _enrich_fn(node_data, api_conns, in_df):
        # Se 'src' fosse stato evictato prematuramente (dopo 'a'), 'b' riceverebbe
        # in_df=None e questo .copy() crasherebbe -> il test fallirebbe.
        out = in_df.copy()
        out["seen_by"] = node_data["id"]
        return out

    node_results = run_enrichment_canvas(
        _FakeSvc(), "p", graph=graph, ctx=object(), api_connections={},
        execution_order=["src", "a", "b"], groups=groups, enrich_fn=_enrich_fn,
    )

    # 'b' ha prodotto correttamente -> 'src' era ancora vivo quando 'b' e' girato
    assert "b" in node_results and list(node_results["b"]["v"]) == [1, 2, 3]
    assert "a" in node_results  # terminale
    # src consumato da entrambi -> evictato a fine run
    assert "src" not in node_results


def test_result_applies_output_columns():
    nodes = {
        "src": ("staticValues", {"id": "src"}),
        "res": ("result", {"id": "res", "output_columns": [
            {"name": "v", "label": "value"}, {"name": "missing"},
        ]}),
    }
    graph = _FakeGraph(nodes, [{"source": "src", "target": "res"}])
    result = run_enrichment_canvas(
        _FakeSvc(), "p", graph=graph, ctx=object(), api_connections={},
        execution_order=["src", "res"], groups=[("single", ["src"]), ("single", ["res"])],
    )
    assert result["res"].to_dict("list") == {"value": [1, 2, 3]}
