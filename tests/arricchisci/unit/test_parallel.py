"""
tests/arricchisci/unit/test_parallel.py
=======================================
Unit test per il modulo parallel.py:
  - should_execute_node: decisione globale (prima riga)
  - get_execute_mask: maschera per-riga
  - compute_parallel_groups: raggruppamento nodi indipendenti
  - execute_parallel_enrichers: esecuzione concorrente + merge

NON richiede backend, API o Celery.
"""

import pytest
import pandas as pd
import numpy as np

pytestmark = pytest.mark.unit

from app.core.arricchisci.parallel import (
    should_execute_node,
    get_execute_mask,
    compute_parallel_groups,
    execute_parallel_enrichers,
    _parse_execute_if,
    _extract_consumed_columns,
    _extract_produced_columns,
)


# =============================================================================
# _parse_execute_if
# =============================================================================

class TestParseExecuteIf:

    def test_none_returns_none(self):
        assert _parse_execute_if({}) is None

    def test_empty_string_returns_none(self):
        assert _parse_execute_if({"execute_if": ""}) is None

    def test_no_equals_returns_none(self):
        assert _parse_execute_if({"execute_if": "just_a_string"}) is None

    def test_simple_equals(self):
        col, expected, negate = _parse_execute_if({"execute_if": "mode=CATASTO"})
        assert col == "mode"
        assert expected == "catasto"  # lowered
        assert negate is False

    def test_not_equals(self):
        col, expected, negate = _parse_execute_if({"execute_if": "mode!=XY"})
        assert col == "mode"
        assert expected == "xy"
        assert negate is True

    def test_strips_spaces(self):
        col, expected, negate = _parse_execute_if({"execute_if": " mode = CATASTO "})
        assert col == "mode"
        assert expected == "catasto"

    def test_reads_from_data_dict(self):
        node = {"data": {"execute_if": "flag=true"}}
        col, expected, negate = _parse_execute_if(node)
        assert col == "flag"
        assert expected == "true"

    def test_flat_overrides_data(self):
        node = {"execute_if": "flat=1", "data": {"execute_if": "nested=2"}}
        col, expected, negate = _parse_execute_if(node)
        assert col == "flat"
        assert expected == "1"


# =============================================================================
# should_execute_node — decisione globale (prima riga)
# =============================================================================

class TestShouldExecuteNode:

    def test_no_condition_always_true(self):
        df = pd.DataFrame({"x": [1]})
        assert should_execute_node({}, df) is True

    def test_match_true(self):
        df = pd.DataFrame({"mode": ["CATASTO"]})
        assert should_execute_node({"execute_if": "mode=CATASTO"}, df) is True

    def test_no_match_false(self):
        df = pd.DataFrame({"mode": ["XY"]})
        assert should_execute_node({"execute_if": "mode=CATASTO"}, df) is False

    def test_case_insensitive(self):
        df = pd.DataFrame({"mode": ["catasto"]})
        assert should_execute_node({"execute_if": "mode=CATASTO"}, df) is True

    def test_negate_match_false(self):
        df = pd.DataFrame({"mode": ["CATASTO"]})
        assert should_execute_node({"execute_if": "mode!=CATASTO"}, df) is False

    def test_negate_no_match_true(self):
        df = pd.DataFrame({"mode": ["XY"]})
        assert should_execute_node({"execute_if": "mode!=CATASTO"}, df) is True

    def test_missing_column_false(self):
        df = pd.DataFrame({"other": [1]})
        assert should_execute_node({"execute_if": "mode=CATASTO"}, df) is False

    def test_missing_column_negate_true(self):
        df = pd.DataFrame({"other": [1]})
        assert should_execute_node({"execute_if": "mode!=CATASTO"}, df) is True

    def test_non_dataframe_input(self):
        assert should_execute_node({"execute_if": "x=1"}, None) is False

    def test_empty_condition(self):
        df = pd.DataFrame({"x": [1]})
        assert should_execute_node({"execute_if": ""}, df) is True


# =============================================================================
# get_execute_mask — maschera per-riga
# =============================================================================

class TestGetExecuteMask:

    @pytest.fixture
    def mixed_df(self):
        return pd.DataFrame({
            "georef_mode": ["CATASTO", "XY", "CATASTO", "INDIRIZZO", "XY"],
            "cod_comune": ["D802", None, "H501", None, None],
        })

    def test_no_condition_all_true(self, mixed_df):
        mask = get_execute_mask({}, mixed_df)
        assert mask.all()
        assert len(mask) == 5

    def test_filter_catasto(self, mixed_df):
        mask = get_execute_mask({"execute_if": "georef_mode=CATASTO"}, mixed_df)
        assert list(mask) == [True, False, True, False, False]

    def test_filter_xy(self, mixed_df):
        mask = get_execute_mask({"execute_if": "georef_mode=XY"}, mixed_df)
        assert list(mask) == [False, True, False, False, True]

    def test_filter_indirizzo(self, mixed_df):
        mask = get_execute_mask({"execute_if": "georef_mode=INDIRIZZO"}, mixed_df)
        assert list(mask) == [False, False, False, True, False]

    def test_negate(self, mixed_df):
        mask = get_execute_mask({"execute_if": "georef_mode!=XY"}, mixed_df)
        assert list(mask) == [True, False, True, True, False]

    def test_missing_column_all_false(self, mixed_df):
        mask = get_execute_mask({"execute_if": "nonexistent=val"}, mixed_df)
        assert not mask.any()

    def test_case_insensitive(self):
        df = pd.DataFrame({"m": ["Catasto", "catasto", "CATASTO"]})
        mask = get_execute_mask({"execute_if": "m=CATASTO"}, df)
        assert mask.all()

    def test_single_row_match(self):
        df = pd.DataFrame({"flag": ["true"]})
        mask = get_execute_mask({"execute_if": "flag=true"}, df)
        assert mask.iloc[0] is True or mask.iloc[0] == True

    def test_single_row_no_match(self):
        df = pd.DataFrame({"flag": ["false"]})
        mask = get_execute_mask({"execute_if": "flag=true"}, df)
        assert mask.iloc[0] is False or mask.iloc[0] == False


# =============================================================================
# _extract_consumed_columns / _extract_produced_columns
# =============================================================================

class TestColumnExtraction:

    def test_consumed_simple_mapping(self):
        node = {"param_mapping": {"x": "X", "y": "Y"}}
        assert _extract_consumed_columns(node) == {"X", "Y"}

    def test_consumed_template(self):
        node = {"param_mapping": {"geometry": "{X},{Y}"}}
        assert _extract_consumed_columns(node) == {"X", "Y"}

    def test_consumed_mixed(self):
        node = {"param_mapping": {"geometry": "{X},{Y}", "anno": "anno"}}
        assert _extract_consumed_columns(node) == {"X", "Y", "anno"}

    def test_consumed_empty(self):
        node = {"param_mapping": {}}
        assert _extract_consumed_columns(node) == set()

    def test_consumed_none(self):
        node = {}
        assert _extract_consumed_columns(node) == set()

    def test_consumed_from_data_dict(self):
        node = {"data": {"param_mapping": {"x": "lon"}}}
        assert _extract_consumed_columns(node) == {"lon"}

    def test_produced_from_output_mapping(self):
        node = {"output_mapping": [
            {"column": "Classificazione"},
            {"column": "COMUNE"},
        ]}
        assert _extract_produced_columns(node) == {"Classificazione", "COMUNE"}

    def test_produced_empty(self):
        assert _extract_produced_columns({}) == set()

    def test_produced_skips_empty_column(self):
        node = {"output_mapping": [{"column": ""}, {"column": "valid"}]}
        assert _extract_produced_columns(node) == {"valid"}


# =============================================================================
# compute_parallel_groups
# =============================================================================

class TestComputeParallelGroups:

    def _make_chain(self):
        """Catena simile a test_completo_ania: static -> catasto -> 9 enricher -> result"""
        nodes = [
            {"id": "static", "type": "staticValues"},
            {"id": "catasto", "type": "api_enrich",
             "param_mapping": {"cod_comune": "cod_comune"},
             "output_mapping": [{"column": "X"}, {"column": "Y"}]},
            {"id": "omi", "type": "api_enrich",
             "param_mapping": {"x": "X", "y": "Y"},
             "output_mapping": [{"column": "nome"}]},
            {"id": "sismica", "type": "api_enrich",
             "param_mapping": {"geometry": "{X},{Y}"},
             "output_mapping": [{"column": "Classificazione"}]},
            {"id": "ingv", "type": "api_enrich",
             "param_mapping": {"geometry": "{X},{Y}"},
             "output_mapping": [{"column": "Pericolosita"}]},
            {"id": "frana", "type": "api_enrich",
             "param_mapping": {"geometry": "{X},{Y}"},
             "output_mapping": [{"column": "Frana"}]},
            {"id": "result", "type": "result"},
        ]
        edges = [
            {"source": "static", "target": "catasto"},
            {"source": "catasto", "target": "omi"},
            {"source": "omi", "target": "sismica"},
            {"source": "sismica", "target": "ingv"},
            {"source": "ingv", "target": "frana"},
            {"source": "frana", "target": "result"},
        ]
        order = ["static", "catasto", "omi", "sismica", "ingv", "frana", "result"]
        return nodes, edges, order

    def test_linear_chain_groups(self):
        nodes, edges, order = self._make_chain()
        groups = compute_parallel_groups(order, nodes, edges)
        # Expected: single(static) + parallel([catasto]) + parallel([omi,sismica,ingv,frana]) + single(result)
        assert groups[0] == ("single", ["static"])
        assert groups[1] == ("parallel", ["catasto"])
        assert groups[2][0] == "parallel"
        assert set(groups[2][1]) == {"omi", "sismica", "ingv", "frana"}
        assert groups[3] == ("single", ["result"])

    def test_single_enricher(self):
        """Un solo nodo enricher -> gruppo parallelo di 1."""
        nodes = [
            {"id": "src", "type": "staticValues"},
            {"id": "e1", "type": "api_enrich",
             "param_mapping": {"x": "X"},
             "output_mapping": [{"column": "Y"}]},
            {"id": "res", "type": "result"},
        ]
        edges = [{"source": "src", "target": "e1"}, {"source": "e1", "target": "res"}]
        groups = compute_parallel_groups(["src", "e1", "res"], nodes, edges)
        assert groups[1] == ("parallel", ["e1"])

    def test_dependent_chain_no_parallelism(self):
        """Se ogni nodo consuma l'output del precedente, nessuna parallelizzazione."""
        nodes = [
            {"id": "src", "type": "staticValues"},
            {"id": "a", "type": "api_enrich",
             "param_mapping": {"x": "input_col"},
             "output_mapping": [{"column": "col_a"}]},
            {"id": "b", "type": "api_enrich",
             "param_mapping": {"x": "col_a"},  # dipende da a
             "output_mapping": [{"column": "col_b"}]},
            {"id": "c", "type": "api_enrich",
             "param_mapping": {"x": "col_b"},  # dipende da b
             "output_mapping": [{"column": "col_c"}]},
            {"id": "res", "type": "result"},
        ]
        edges = [
            {"source": "src", "target": "a"},
            {"source": "a", "target": "b"},
            {"source": "b", "target": "c"},
            {"source": "c", "target": "res"},
        ]
        groups = compute_parallel_groups(["src", "a", "b", "c", "res"], nodes, edges)
        # Ogni enricher dipende dal precedente -> gruppi da 1
        enricher_groups = [g for g in groups if g[0] == "parallel"]
        assert all(len(g[1]) == 1 for g in enricher_groups)

    def test_empty_canvas(self):
        groups = compute_parallel_groups([], [], [])
        assert groups == []

    def test_no_enrichers(self):
        nodes = [{"id": "src", "type": "staticValues"}, {"id": "res", "type": "result"}]
        edges = [{"source": "src", "target": "res"}]
        groups = compute_parallel_groups(["src", "res"], nodes, edges)
        assert len(groups) == 2
        assert all(g[0] == "single" for g in groups)


# =============================================================================
# execute_parallel_enrichers
# =============================================================================

class TestExecuteParallelEnrichers:

    def test_single_node_no_threads(self):
        """Un solo nodo: niente ThreadPoolExecutor, esecuzione diretta."""
        input_df = pd.DataFrame({"X": [8.17], "Y": [44.97]})
        node_map = {
            "n1": {"id": "n1", "param_mapping": {"x": "X"},
                   "output_mapping": [{"column": "result_col"}]},
        }

        def mock_execute(node_data, api_conns, df):
            out = df.copy()
            out["result_col"] = "value_1"
            return out

        results, error = execute_parallel_enrichers(
            node_ids=["n1"],
            input_df=input_df,
            node_map=node_map,
            api_connections={},
            execute_single_fn=mock_execute,
        )
        assert error is None
        assert "result_col" in results["n1"].columns
        assert results["n1"]["result_col"].iloc[0] == "value_1"

    def test_parallel_merge_columns(self):
        """Due nodi in parallelo: colonne diverse mergiate."""
        input_df = pd.DataFrame({"X": [8.17], "Y": [44.97]})
        node_map = {
            "n1": {"id": "n1", "param_mapping": {"x": "X"},
                   "output_mapping": [{"column": "col_a"}]},
            "n2": {"id": "n2", "param_mapping": {"x": "X"},
                   "output_mapping": [{"column": "col_b"}]},
        }

        def mock_execute(node_data, api_conns, df):
            out = df.copy()
            col = node_data["output_mapping"][0]["column"]
            out[col] = f"val_{col}"
            return out

        results, error = execute_parallel_enrichers(
            node_ids=["n1", "n2"],
            input_df=input_df,
            node_map=node_map,
            api_connections={},
            execute_single_fn=mock_execute,
        )
        assert error is None
        merged = results["n1"]  # all nodes point to same merged df
        assert "col_a" in merged.columns
        assert "col_b" in merged.columns
        assert merged["col_a"].iloc[0] == "val_col_a"
        assert merged["col_b"].iloc[0] == "val_col_b"

    def test_execute_if_skip_in_parallel(self):
        """Nodo con execute_if=false viene skippato, altri eseguiti."""
        input_df = pd.DataFrame({"mode": ["XY"], "X": [8.17]})
        node_map = {
            "catasto": {"id": "catasto",
                        "execute_if": "mode=CATASTO",
                        "param_mapping": {"x": "X"},
                        "output_mapping": [{"column": "catasto_col"}]},
            "sismica": {"id": "sismica",
                        "param_mapping": {"x": "X"},
                        "output_mapping": [{"column": "sismica_col"}]},
        }

        def mock_execute(node_data, api_conns, df):
            out = df.copy()
            col = node_data["output_mapping"][0]["column"]
            out[col] = "populated"
            return out

        results, error = execute_parallel_enrichers(
            node_ids=["catasto", "sismica"],
            input_df=input_df,
            node_map=node_map,
            api_connections={},
            execute_single_fn=mock_execute,
        )
        assert error is None
        merged = results["catasto"]
        # catasto skippato -> la sua colonna non e presente (None nel merge)
        # sismica eseguito -> colonna presente
        assert "sismica_col" in merged.columns
        assert merged["sismica_col"].iloc[0] == "populated"

    def test_per_row_partial_execution(self):
        """execute_if filtra per-riga: solo righe matching vengono arricchite."""
        input_df = pd.DataFrame({
            "mode": ["CATASTO", "XY", "CATASTO"],
            "X": [None, 8.17, None],
        })
        node_map = {
            "cat": {"id": "cat",
                    "execute_if": "mode=CATASTO",
                    "param_mapping": {"x": "X"},
                    "output_mapping": [{"column": "enriched"}]},
        }

        def mock_execute(node_data, api_conns, df):
            out = df.copy()
            out["enriched"] = "yes"
            return out

        results, error = execute_parallel_enrichers(
            node_ids=["cat"],
            input_df=input_df,
            node_map=node_map,
            api_connections={},
            execute_single_fn=mock_execute,
        )
        assert error is None
        merged = results["cat"]
        assert len(merged) == 3
        # Riga 0 (CATASTO): enriched = "yes"
        assert merged.loc[0, "enriched"] == "yes"
        # Riga 1 (XY): enriched = NaN/None (skippata — pd.concat converte None in NaN)
        assert pd.isna(merged.loc[1, "enriched"])
        # Riga 2 (CATASTO): enriched = "yes"
        assert merged.loc[2, "enriched"] == "yes"

    def test_error_propagation(self):
        """Errore in un nodo viene propagato come stringa."""
        input_df = pd.DataFrame({"X": [1]})
        node_map = {
            "bad": {"id": "bad", "param_mapping": {"x": "X"},
                    "output_mapping": [{"column": "c"}]},
        }

        def mock_execute(node_data, api_conns, df):
            raise ValueError("API down")

        results, error = execute_parallel_enrichers(
            node_ids=["bad"],
            input_df=input_df,
            node_map=node_map,
            api_connections={},
            execute_single_fn=mock_execute,
        )
        assert error is not None
        assert "API down" in error
