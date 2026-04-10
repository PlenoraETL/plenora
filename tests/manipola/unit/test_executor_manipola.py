"""
tests/manipola/unit/test_executor_manipola.py
=============================================
Unit test sui metodi helper di ManipolaExecutor.

NON richiede backend, API o Celery.
Testa metodi puri che non fanno I/O.

Obiettivi coverage:
  - _ordina_topologicamente: ordinamento Kahn, cicli, nodi disconnessi
  - _esegui_multi_join: < 2 input, join_keys esplicite/auto-detect, duplicati
  - _gestisci_join_default: singola sorgente, zero sorgenti, multiple
  - _costruisci_mappa_tipi_canvas: nodi con colonne, alias, calculated_columns
  - _costruisci_colonne_con_tipi: lookup esatto/lower/suffissi, dtype fallback
  - _estrai_srid_da_canvas: SRID da geometry type, nessun SRID
  - _applica_group_by: abilitato/disabilitato, funzioni aggregate
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock
from typing import List

pytestmark = pytest.mark.unit

from app.core.manipola.executor import ManipolaExecutor
from app.shared.models import Node, Edge, Column, Position, CanvasState


# ─── Helper ───────────────────────────────────────────────────────────────────

_POS = Position(x=0, y=0)


def make_executor():
    return ManipolaExecutor(project_service=MagicMock())


def make_node(node_id, node_type="transform", columns=None, calc_cols=None):
    cols = [Column(name=c["name"], type=c.get("type","str"),
                   visible=True, alias=c.get("alias"))
            for c in (columns or [])]
    return Node(id=node_id, type=node_type, position=_POS,
                columns=cols,
                calculated_columns=calc_cols or [])


def make_edge(src, tgt):
    return Edge(id=f"{src}-{tgt}", source=src, target=tgt)


def make_canvas(nodes, edges):
    return CanvasState(nodes=nodes, edges=edges)


# =============================================================================
# _ordina_topologicamente
# =============================================================================

class TestOrdinaTopologicamente:

    def test_pipeline_lineare(self):
        """A → B → C → D ordine corretto."""
        ex = make_executor()
        nodes = [make_node("A"), make_node("B"), make_node("C"), make_node("D")]
        edges = [make_edge("A","B"), make_edge("B","C"), make_edge("C","D")]
        result = ex._ordina_topologicamente(nodes, edges, {})
        ids = [n.id for n in result]
        assert ids.index("A") < ids.index("B")
        assert ids.index("B") < ids.index("C")
        assert ids.index("C") < ids.index("D")

    def test_pipeline_con_fork(self):
        """A → B, A → C, B → D, C → D."""
        ex = make_executor()
        nodes = [make_node("A"), make_node("B"), make_node("C"), make_node("D")]
        edges = [make_edge("A","B"), make_edge("A","C"),
                 make_edge("B","D"), make_edge("C","D")]
        result = ex._ordina_topologicamente(nodes, edges, {})
        ids = [n.id for n in result]
        assert ids.index("A") < ids.index("B")
        assert ids.index("A") < ids.index("C")
        assert ids.index("B") < ids.index("D")
        assert ids.index("C") < ids.index("D")

    def test_nodo_singolo(self):
        ex = make_executor()
        nodes = [make_node("A")]
        result = ex._ordina_topologicamente(nodes, [], {})
        assert len(result) == 1
        assert result[0].id == "A"

    def test_ciclo_raise_valueerror(self):
        """Ciclo A→B→A → ValueError."""
        ex = make_executor()
        nodes = [make_node("A"), make_node("B")]
        edges = [make_edge("A","B"), make_edge("B","A")]
        with pytest.raises(ValueError, match="Ciclo"):
            ex._ordina_topologicamente(nodes, edges, {})

    def test_tutti_i_nodi_nel_risultato(self):
        ex = make_executor()
        nodes = [make_node("A"), make_node("B"), make_node("C")]
        edges = [make_edge("A","B"), make_edge("B","C")]
        result = ex._ordina_topologicamente(nodes, edges, {})
        assert len(result) == 3

    def test_nodi_disconnessi_tutti_inclusi(self):
        """Nodi senza edge → tutti nel risultato."""
        ex = make_executor()
        nodes = [make_node("A"), make_node("B"), make_node("C")]
        result = ex._ordina_topologicamente(nodes, [], {})
        assert len(result) == 3


# =============================================================================
# _esegui_multi_join
# =============================================================================

class TestEseguiMultiJoin:

    def _node_with_keys(self, keys, join_type="left"):
        node = make_node("join")
        node.join_keys = keys
        node.multi_join_type = join_type
        return node

    def test_meno_di_2_input_passthrough(self):
        """Mutation target: < 2 input → restituisce il primo."""
        ex = make_executor()
        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        node = self._node_with_keys(["id"])
        result = ex._esegui_multi_join(node, [df])
        pd.testing.assert_frame_equal(result, df)

    def test_zero_input_dataframe_vuoto(self):
        ex = make_executor()
        node = self._node_with_keys([])
        result = ex._esegui_multi_join(node, [])
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_join_esplicito_due_df(self):
        ex = make_executor()
        df1 = pd.DataFrame({"id": [1, 2, 3], "nome": ["a", "b", "c"]})
        df2 = pd.DataFrame({"id": [1, 2, 4], "valore": [10, 20, 40]})
        node = self._node_with_keys(["id"], join_type="inner")
        result = ex._esegui_multi_join(node, [df1, df2])
        assert "nome" in result.columns
        assert "valore" in result.columns
        assert len(result) == 2   # inner join: solo id 1,2

    def test_left_join_preserva_tutto(self):
        ex = make_executor()
        df1 = pd.DataFrame({"id": [1, 2, 3], "nome": ["a", "b", "c"]})
        df2 = pd.DataFrame({"id": [1, 2],     "valore": [10, 20]})
        node = self._node_with_keys(["id"], join_type="left")
        result = ex._esegui_multi_join(node, [df1, df2])
        assert len(result) == 3   # left join: tutte le righe di df1

    def test_colonne_duplicate_rinominate(self):
        """Colonne non-chiave duplicate → rinominate con suffisso _N."""
        ex = make_executor()
        df1 = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        df2 = pd.DataFrame({"id": [1, 2], "nome": ["x", "y"], "extra": [10, 20]})
        node = self._node_with_keys(["id"], join_type="inner")
        result = ex._esegui_multi_join(node, [df1, df2])
        assert "nome" in result.columns
        assert "nome_2" in result.columns

    def test_auto_detect_chiavi_comuni(self):
        """Senza join_keys esplicite → rileva colonne comuni."""
        ex = make_executor()
        df1 = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        df2 = pd.DataFrame({"id": [1, 2], "valore": [10, 20]})
        node = make_node("join")
        node.join_keys = []
        node.multi_join_type = "inner"
        result = ex._esegui_multi_join(node, [df1, df2])
        assert "id" in result.columns
        assert "nome" in result.columns
        assert "valore" in result.columns

    def test_nessuna_colonna_comune_passthrough(self):
        """Nessuna colonna comune → restituisce il primo."""
        ex = make_executor()
        df1 = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        df2 = pd.DataFrame({"c": [3, 4], "d": ["z", "w"]})
        node = make_node("join")
        node.join_keys = []
        node.multi_join_type = "inner"
        result = ex._esegui_multi_join(node, [df1, df2])
        assert list(result.columns) == ["a", "b"]

    def test_chiavi_mancanti_in_df_passthrough(self):
        """Chiave non presente in uno dei DF → restituisce il primo."""
        ex = make_executor()
        df1 = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        df2 = pd.DataFrame({"altro": [1, 2], "valore": [10, 20]})
        node = self._node_with_keys(["id"])
        result = ex._esegui_multi_join(node, [df1, df2])
        pd.testing.assert_frame_equal(result, df1)


# =============================================================================
# _gestisci_join_default
# =============================================================================

class TestGestisciJoinDefault:

    def test_singola_sorgente_passthrough(self):
        ex = make_executor()
        df = pd.DataFrame({"id": [1, 2]})
        result = ex._gestisci_join_default({"src": df})
        pd.testing.assert_frame_equal(result, df)

    def test_zero_sorgenti_raise(self):
        ex = make_executor()
        with pytest.raises(ValueError):
            ex._gestisci_join_default({})

    def test_multiple_sorgenti_raise(self):
        """Mutation target: 2+ sorgenti senza Transform → ValueError."""
        ex = make_executor()
        df = pd.DataFrame({"id": [1]})
        with pytest.raises(ValueError, match="sorgenti"):
            ex._gestisci_join_default({"a": df, "b": df})


# =============================================================================
# _costruisci_mappa_tipi_canvas
# =============================================================================

class TestCostruisciMappaTipiCanvas:

    def test_tipi_base(self):
        ex = make_executor()
        node = make_node("s1", columns=[
            {"name": "id",   "type": "int"},
            {"name": "nome", "type": "str"},
            {"name": "geom", "type": "geometry:point:4326"},
        ])
        canvas = make_canvas([node], [])
        m = ex._costruisci_mappa_tipi_canvas(canvas)
        assert m["id"]   == "int"
        assert m["nome"] == "str"
        assert m["geom"] == "geometry:point:4326"

    def test_alias_mappato(self):
        """Mutation target: alias → mappa sia alias che nome originale."""
        ex = make_executor()
        node = make_node("s1", columns=[
            {"name": "nome_cliente", "type": "str", "alias": "cliente"},
        ])
        canvas = make_canvas([node], [])
        m = ex._costruisci_mappa_tipi_canvas(canvas)
        assert "cliente" in m
        assert m["cliente"] == "str"

    def test_suffissi_L_R_aggiunti(self):
        """Mutation target: ogni colonna ha anche versione _L e _R."""
        ex = make_executor()
        node = make_node("s1", columns=[{"name": "id", "type": "int"}])
        canvas = make_canvas([node], [])
        m = ex._costruisci_mappa_tipi_canvas(canvas)
        assert "id_L" in m
        assert "id_R" in m
        assert m["id_L"] == "int"
        assert m["id_R"] == "int"

    def test_calculated_columns_incluse(self):
        ex = make_executor()
        node = make_node("s1",
            columns=[{"name": "id", "type": "int"}],
            calc_cols=[{"name": "area_buffer", "output_type": "real"}]
        )
        canvas = make_canvas([node], [])
        m = ex._costruisci_mappa_tipi_canvas(canvas)
        assert "area_buffer" in m
        assert m["area_buffer"] == "real"

    def test_canvas_vuoto(self):
        ex = make_executor()
        canvas = make_canvas([], [])
        m = ex._costruisci_mappa_tipi_canvas(canvas)
        assert m == {}

    def test_nodo_senza_colonne_non_crasha(self):
        ex = make_executor()
        node = make_node("s1")  # nessuna colonna
        canvas = make_canvas([node], [])
        m = ex._costruisci_mappa_tipi_canvas(canvas)
        assert isinstance(m, dict)


# =============================================================================
# _costruisci_colonne_con_tipi
# =============================================================================

class TestCostruisciColonneConTipi:

    def test_match_esatto(self):
        ex = make_executor()
        df = pd.DataFrame({"id": [1], "nome": ["a"]})
        canvas_map = {"id": "int", "nome": "str"}
        result = ex._costruisci_colonne_con_tipi(df, canvas_map)
        types = {c["name"]: c["type"] for c in result}
        assert types["id"]   == "int"
        assert types["nome"] == "str"

    def test_match_case_insensitive(self):
        """Oracle → UPPERCASE, canvas → lowercase: deve matchare."""
        ex = make_executor()
        df = pd.DataFrame({"ID": [1], "NOME": ["a"]})
        canvas_map = {"id": "int", "nome": "str"}
        result = ex._costruisci_colonne_con_tipi(df, canvas_map)
        types = {c["name"]: c["type"] for c in result}
        assert types["ID"]   == "int"
        assert types["NOME"] == "str"

    def test_match_suffisso_L(self):
        """Colonna con _L → lookup della base."""
        ex = make_executor()
        df = pd.DataFrame({"nome_L": ["a", "b"]})
        canvas_map = {"nome": "str"}
        result = ex._costruisci_colonne_con_tipi(df, canvas_map)
        assert result[0]["type"] == "str"

    def test_match_suffisso_R(self):
        ex = make_executor()
        df = pd.DataFrame({"id_R": [1, 2]})
        canvas_map = {"id": "int"}
        result = ex._costruisci_colonne_con_tipi(df, canvas_map)
        assert result[0]["type"] == "int"

    def test_fallback_dtype_int(self):
        ex = make_executor()
        df = pd.DataFrame({"valore": [1, 2, 3]})
        result = ex._costruisci_colonne_con_tipi(df, {})
        assert result[0]["type"] == "int"

    def test_fallback_dtype_float(self):
        ex = make_executor()
        df = pd.DataFrame({"prezzo": [1.5, 2.3]})
        result = ex._costruisci_colonne_con_tipi(df, {})
        assert result[0]["type"] == "real"

    def test_fallback_dtype_bool(self):
        ex = make_executor()
        df = pd.DataFrame({"flag": [True, False]})
        result = ex._costruisci_colonne_con_tipi(df, {})
        assert result[0]["type"] == "bool"

    def test_canvas_type_vince_su_dtype(self):
        """Mutation target: tipo canvas deve vincere su dtype pandas."""
        ex = make_executor()
        df = pd.DataFrame({"geom": ["POINT(1 2)", "POINT(3 4)"]})  # dtype=object
        canvas_map = {"geom": "geometry:point:4326"}
        result = ex._costruisci_colonne_con_tipi(df, canvas_map)
        assert result[0]["type"] == "geometry:point:4326"
        assert result[0]["type"] != "str"


# =============================================================================
# _estrai_srid_da_canvas
# =============================================================================

class TestEstraiSridDaCanvas:

    def test_srid_da_geometry_type(self):
        ex = make_executor()
        node = make_node("s1", columns=[
            {"name": "id",   "type": "int"},
            {"name": "geom", "type": "geometry:point:4326"},
        ])
        canvas = make_canvas([node], [])
        srid = ex._estrai_srid_da_canvas(canvas)
        assert srid == 4326

    def test_srid_32632(self):
        ex = make_executor()
        node = make_node("s1", columns=[
            {"name": "geom", "type": "geometry:polygon:32632"},
        ])
        canvas = make_canvas([node], [])
        srid = ex._estrai_srid_da_canvas(canvas)
        assert srid == 32632

    def test_nessun_srid_restituisce_none(self):
        ex = make_executor()
        node = make_node("s1", columns=[
            {"name": "id",   "type": "int"},
            {"name": "nome", "type": "str"},
        ])
        canvas = make_canvas([node], [])
        srid = ex._estrai_srid_da_canvas(canvas)
        assert srid is None

    def test_geometry_senza_srid_restituisce_none(self):
        ex = make_executor()
        node = make_node("s1", columns=[
            {"name": "geom", "type": "geometry"},
        ])
        canvas = make_canvas([node], [])
        srid = ex._estrai_srid_da_canvas(canvas)
        assert srid is None

    def test_canvas_vuoto_restituisce_none(self):
        ex = make_executor()
        canvas = make_canvas([], [])
        srid = ex._estrai_srid_da_canvas(canvas)
        assert srid is None


# =============================================================================
# _applica_group_by
# =============================================================================

class TestApplicaGroupBy:

    def _result_node(self, enabled=True, group_cols=None, aggregations=None):
        node = make_node("result", "result")
        node.group_by_enabled = enabled
        node.group_by_columns = group_cols or []
        node.aggregations = aggregations or []
        return node

    def test_disabilitato_restituisce_invariato(self):
        ex = make_executor()
        df = pd.DataFrame({"comune": ["Roma", "Milano"], "valore": [100, 200]})
        node = self._result_node(enabled=False)
        result = ex._applica_group_by(df, node)
        pd.testing.assert_frame_equal(result, df)

    def test_group_cols_e_aggregations_vuoti(self):
        ex = make_executor()
        df = pd.DataFrame({"comune": ["Roma", "Milano"], "valore": [100, 200]})
        node = self._result_node(enabled=True, group_cols=[], aggregations=[])
        result = ex._applica_group_by(df, node)
        pd.testing.assert_frame_equal(result, df)

    def test_count_per_gruppo(self):
        """
        Mutation target: COUNT deve preservare la colonna di group_by.
        Fix B-BE-MANIPOLA-01: usare size() invece di count su df.columns[0].
        """
        ex = make_executor()
        df = pd.DataFrame({
            "comune": ["Roma", "Roma", "Milano"],
            "valore": [100, 200, 300],
        })
        node = self._result_node(
            enabled=True,
            group_cols=["comune"],
            aggregations=[{"column": "valore", "function": "count", "alias": "n"}]
        )
        result = ex._applica_group_by(df, node)
        assert "comune" in result.columns   # colonna group_by deve esserci
        assert "n" in result.columns
        assert len(result) == 2
        assert sorted(result["n"].tolist()) == [1, 2]

    def test_sum_per_gruppo(self):
        ex = make_executor()
        df = pd.DataFrame({
            "comune": ["Roma", "Roma", "Milano"],
            "valore": [100.0, 200.0, 300.0],
        })
        node = self._result_node(
            enabled=True,
            group_cols=["comune"],
            aggregations=[{"column": "valore", "function": "sum", "alias": "tot"}]
        )
        result = ex._applica_group_by(df, node)
        assert "tot" in result.columns
        roma_tot = result[result["comune"] == "Roma"]["tot"].iloc[0]
        assert abs(roma_tot - 300.0) < 0.01

    def test_colonna_aggregazione_inesistente_skippata(self):
        ex = make_executor()
        df = pd.DataFrame({"comune": ["Roma", "Milano"], "valore": [100, 200]})
        node = self._result_node(
            enabled=True,
            group_cols=["comune"],
            aggregations=[{"column": "COLONNA_XYZ", "function": "sum", "alias": "x"}]
        )
        # Non deve crashare
        result = ex._applica_group_by(df, node)
        assert len(result) >= 1
