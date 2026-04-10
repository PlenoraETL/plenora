"""
tests/estrai/unit/test_executor_helpers.py
===========================================
Unit test sui metodi helper di EstraiExecutor.

Testa i metodi puri senza bisogno di connessione DB o API:
  - _normalize_geometry_type()
  - _get_visible_columns()
  - _is_column_visible()
  - _build_canvas_type_map()
  - _costruisci_colonne_con_tipi_canvas()
  - _rimuovi_limit()  (versione EstraiExecutor)
  - _costruisci_sql_preview()

EstraiExecutor richiede dipendenze nel costruttore, quindi usiamo un mock
minimale che non fa nulla.

Obiettivi coverage:
  - Rami ArcGIS helper (feature_source type) mai coperti dagli integration test
  - _normalize_geometry_type: tutti i formati di input (esri, generico, già normalizzato)
  - _build_canvas_type_map: alias, calculated_columns, case-insensitive
  - Mutation score: assert sui valori esatti dei tipi restituiti
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock

pytestmark = pytest.mark.unit

from app.core.estrai.executor import EstraiExecutor
from app.shared.models import Node, Column, Position


# ─── Setup executor con mock ──────────────────────────────────────────────────

_POS = Position(x=0, y=0)


@pytest.fixture
def ex():
    """EstraiExecutor con tutte le dipendenze mockate."""
    return EstraiExecutor(
        project_service=MagicMock(),
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        features_service=None,
    )


def make_node(**kwargs) -> Node:
    """Helper: crea Node minimale."""
    defaults = dict(id="n1", type="source", position=_POS, columns=[], filters=[])
    defaults.update(kwargs)
    return Node(**defaults)


# =============================================================================
# _normalize_geometry_type
# =============================================================================

class TestNormalizeGeometryType:

    def test_gia_normalizzato_polygon(self, ex):
        assert ex._normalize_geometry_type("geometry:polygon") == "geometry:polygon"

    def test_gia_normalizzato_point(self, ex):
        assert ex._normalize_geometry_type("geometry:point") == "geometry:point"

    def test_gia_normalizzato_con_srid(self, ex):
        assert ex._normalize_geometry_type("geometry:point:4326") == "geometry:point:4326"

    def test_stringa_vuota(self, ex):
        assert ex._normalize_geometry_type("") == "geometry"

    def test_none(self, ex):
        assert ex._normalize_geometry_type(None) == "geometry"

    # Formato ArcGIS esri
    def test_esri_polygon(self, ex):
        assert ex._normalize_geometry_type("esriGeometryPolygon") == "geometry:polygon"

    def test_esri_point(self, ex):
        assert ex._normalize_geometry_type("esriGeometryPoint") == "geometry:point"

    def test_esri_polyline(self, ex):
        assert ex._normalize_geometry_type("esriGeometryPolyline") == "geometry:line"

    def test_esri_multipoint(self, ex):
        assert ex._normalize_geometry_type("esriGeometryMultipoint") == "geometry:multipoint"

    def test_esri_case_insensitive(self, ex):
        """Mutation target: case-fold deve avvenire prima della lookup."""
        result = ex._normalize_geometry_type("ESRIGEOMETRYPOLYGON")
        assert result == "geometry:polygon"

    # Formato generico
    def test_generico_polygon(self, ex):
        assert ex._normalize_geometry_type("Polygon") == "geometry:polygon"

    def test_generico_point(self, ex):
        assert ex._normalize_geometry_type("Point") == "geometry:point"

    def test_generico_multipolygon(self, ex):
        assert ex._normalize_geometry_type("MultiPolygon") == "geometry:multipolygon"

    def test_generico_linestring(self, ex):
        assert ex._normalize_geometry_type("LineString") == "geometry:line"

    def test_generico_multilinestring(self, ex):
        assert ex._normalize_geometry_type("MultiLineString") == "geometry:line"

    def test_tipo_sconosciuto(self, ex):
        """Tipo sconosciuto → fallback 'geometry'."""
        result = ex._normalize_geometry_type("TIPO_SCONOSCIUTO_XYZ")
        assert result == "geometry"


# =============================================================================
# _get_visible_columns (da nodo feature_source)
# =============================================================================

class TestGetVisibleColumns:

    def _feature_node(self, columns_data):
        """Crea nodo feature_source con colonne come lista di dict."""
        node = make_node(type="feature_source", table_name=None)
        node.data = {"columns": columns_data}
        return node

    def test_tutte_visibili(self, ex):
        node = self._feature_node([
            {"name": "id",   "visible": True},
            {"name": "nome", "visible": True},
        ])
        result = ex._get_visible_columns(node)
        assert result == ["id", "nome"]

    def test_alcune_nascoste(self, ex):
        node = self._feature_node([
            {"name": "id",      "visible": True},
            {"name": "segreto", "visible": False},
            {"name": "nome",    "visible": True},
        ])
        result = ex._get_visible_columns(node)
        assert "id" in result
        assert "nome" in result
        assert "segreto" not in result

    def test_nessuna_colonna(self, ex):
        node = self._feature_node([])
        result = ex._get_visible_columns(node)
        assert result == []

    def test_visible_default_true(self, ex):
        """Se visible non è specificato → default True (inclusa)."""
        node = self._feature_node([{"name": "col"}])
        result = ex._get_visible_columns(node)
        assert "col" in result

    def test_nodo_senza_data(self, ex):
        node = make_node(type="feature_source", table_name=None)
        result = ex._get_visible_columns(node)
        assert result == []


# =============================================================================
# _is_column_visible
# =============================================================================

class TestIsColumnVisible:

    def _node_with_cols(self, cols_data):
        node = make_node(type="feature_source", table_name=None)
        node.data = {"columns": cols_data}
        return node

    def test_colonna_visibile_true(self, ex):
        node = self._node_with_cols([{"name": "geom", "visible": True}])
        assert ex._is_column_visible(node, "geom") is True

    def test_colonna_visibile_false(self, ex):
        """Mutation target: colonna esplicitamente False → False."""
        node = self._node_with_cols([{"name": "geom", "visible": False}])
        assert ex._is_column_visible(node, "geom") is False

    def test_colonna_non_trovata_default_true(self, ex):
        """Mutation target: colonna non in lista → True (default visible)."""
        node = self._node_with_cols([{"name": "altro", "visible": False}])
        assert ex._is_column_visible(node, "colonna_inesistente") is True

    def test_nodo_senza_colonne_default_true(self, ex):
        node = make_node(type="feature_source", table_name=None)
        assert ex._is_column_visible(node, "qualsiasi") is True


# =============================================================================
# _build_canvas_type_map
# =============================================================================

class TestBuildCanvasTypeMap:

    def _canvas_with_nodes(self, nodes):
        from app.shared.models import CanvasState
        return CanvasState(nodes=nodes, edges=[])

    def test_tipi_base(self, ex):
        node = make_node(id="s1", type="source", table_name="tab",
                         columns=[
                             Column(name="id",   type="int",  visible=True),
                             Column(name="nome", type="str",  visible=True),
                             Column(name="geom", type="geometry:point:4326", visible=True),
                         ])
        canvas = self._canvas_with_nodes([node])
        m = ex._build_canvas_type_map(canvas)
        assert m["id"]   == "int"
        assert m["nome"] == "str"
        assert m["geom"] == "geometry:point:4326"

    def test_alias_mappato_con_alias(self, ex):
        """Mutation target: colonna con alias → mappa anche nome alias."""
        node = make_node(id="s1", type="source", table_name="tab",
                         columns=[
                             Column(name="nome_cliente", type="str",
                                    visible=True, alias="cliente"),
                         ])
        canvas = self._canvas_with_nodes([node])
        m = ex._build_canvas_type_map(canvas)
        assert "cliente" in m
        assert m["cliente"] == "str"

    def test_calculated_columns_incluse(self, ex):
        """Colonne calcolate con output_type → incluse nella mappa."""
        node = make_node(id="s1", type="source", table_name="tab",
                         columns=[Column(name="id", type="int", visible=True)],
                         calculated_columns=[
                             {"name": "area_buffer", "output_type": "real"},
                         ])
        canvas = self._canvas_with_nodes([node])
        m = ex._build_canvas_type_map(canvas)
        assert "area_buffer" in m
        assert m["area_buffer"] == "real"

    def test_canvas_vuoto(self, ex):
        canvas = self._canvas_with_nodes([])
        m = ex._build_canvas_type_map(canvas)
        assert m == {}

    def test_feature_source_geometry_type(self, ex):
        """Nodo feature_source con geometry_type → geometry normalizzato."""
        node = make_node(id="f1", type="feature_source",
                         table_name=None,
                         geometry_type="esriGeometryPolygon",
                         columns=[])
        canvas = self._canvas_with_nodes([node])
        m = ex._build_canvas_type_map(canvas)
        # geometry type deve essere in mappa
        assert "geometry" in m
        assert "polygon" in m["geometry"].lower()


# =============================================================================
# _costruisci_colonne_con_tipi_canvas
# =============================================================================

class TestCostruisciColonneConTipiCanvas:

    def test_match_esatto(self, ex):
        df = pd.DataFrame({"id": [1], "nome": ["a"]})
        canvas_map = {"id": "int", "nome": "str"}
        result = ex._costruisci_colonne_con_tipi_canvas(df, canvas_map)
        types = {c["name"]: c["type"] for c in result}
        assert types["id"]   == "int"
        assert types["nome"] == "str"

    def test_match_case_insensitive(self, ex):
        """Oracle restituisce colonne UPPERCASE → match case-insensitive."""
        df = pd.DataFrame({"ID": [1], "NOME": ["a"]})
        canvas_map = {"id": "int", "nome": "str"}   # lowercase nel canvas
        result = ex._costruisci_colonne_con_tipi_canvas(df, canvas_map)
        types = {c["name"]: c["type"] for c in result}
        assert types["ID"]   == "int"
        assert types["NOME"] == "str"

    def test_fallback_dtype_int(self, ex):
        """Colonna non nel canvas → inferenza da dtype pandas."""
        df = pd.DataFrame({"valore": [1, 2, 3]})   # int64
        result = ex._costruisci_colonne_con_tipi_canvas(df, {})
        assert result[0]["type"] == "int"

    def test_fallback_dtype_float(self, ex):
        df = pd.DataFrame({"prezzo": [1.5, 2.3]})   # float64
        result = ex._costruisci_colonne_con_tipi_canvas(df, {})
        assert result[0]["type"] == "real"

    def test_fallback_dtype_bool(self, ex):
        df = pd.DataFrame({"flag": [True, False]})
        result = ex._costruisci_colonne_con_tipi_canvas(df, {})
        assert result[0]["type"] == "bool"

    def test_fallback_dtype_object_str(self, ex):
        df = pd.DataFrame({"nome": ["a", "b"]})   # object
        result = ex._costruisci_colonne_con_tipi_canvas(df, {})
        assert result[0]["type"] == "str"

    def test_canvas_type_prioritario_su_dtype(self, ex):
        """Mutation target: il tipo canvas deve vincere sul dtype pandas."""
        df = pd.DataFrame({"geom": ["POINT(1 2)", "POINT(3 4)"]})  # dtype=object → str
        canvas_map = {"geom": "geometry:point:4326"}
        result = ex._costruisci_colonne_con_tipi_canvas(df, canvas_map)
        assert result[0]["type"] == "geometry:point:4326"
        assert result[0]["type"] != "str"   # se mutato, fallisce


# =============================================================================
# _rimuovi_limit (versione EstraiExecutor — identica a QueryGenerator)
# =============================================================================

class TestRimuoviLimitExecutor:

    def test_rimuove_limit(self, ex):
        q = "SELECT id FROM tab LIMIT 100"
        assert "LIMIT" not in ex._rimuovi_limit(q)

    def test_rimuove_fetch_first(self, ex):
        q = "SELECT id FROM tab FETCH FIRST 10 ROWS ONLY"
        r = ex._rimuovi_limit(q)
        assert "FETCH" not in r

    def test_rimuove_select_top(self, ex):
        q = "SELECT TOP 25 id FROM tab"
        r = ex._rimuovi_limit(q)
        assert "TOP 25" not in r

    def test_query_senza_limit_invariata(self, ex):
        q = "SELECT id FROM tab WHERE id > 1"
        assert ex._rimuovi_limit(q) == q


# =============================================================================
# _costruisci_sql_preview (versione EstraiExecutor)
# =============================================================================

class TestCostruisciSqlPreviewExecutor:

    def test_postgres_limit_plus_1(self, ex):
        p, c = ex._costruisci_sql_preview("SELECT id FROM tab", 50, "postgres")
        assert "LIMIT 51" in p

    def test_oracle_fetch_first(self, ex):
        p, c = ex._costruisci_sql_preview("SELECT id FROM tab", 20, "oracle")
        assert "FETCH FIRST 21 ROWS ONLY" in p

    def test_mssql_top(self, ex):
        p, c = ex._costruisci_sql_preview("SELECT id FROM tab", 15, "mssql")
        assert "TOP 16" in p

    def test_count_sql_contiene_subquery(self, ex):
        """count_sql deve wrappare la query originale come subquery."""
        _, c = ex._costruisci_sql_preview("SELECT id FROM tab", 10, "postgres")
        assert "COUNT" in c
        assert "SELECT" in c

    def test_db_sconosciuto_raise(self, ex):
        with pytest.raises(ValueError):
            ex._costruisci_sql_preview("SELECT 1", 10, "db_xyz_inesistente")
