"""
tests/estrai/unit/test_executor_coverage.py
============================================
Test mirati a coprire le righe mancanti in executor.py (coverage attuale 46%).

Zona coperta:
  - _resolve_connection: percorso ArcGIS, ValueError connessione non trovata
  - execute(): no result_node, no metadata, connessione non trovata, percorso arcgis
  - _execute_feature_service(): senza feature_source, con feature_source, eccezione
  - preview(): stessi branch di execute
  - _preview_feature_service(): senza feature_source, con feature_source, troncamento
  - _salva_tipi_colonne_da_canvas(): con e senza colonne nel risultato
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

from app.core.estrai.executor import EstraiExecutor
from app.shared.models import Node, Column, Edge, Position, CanvasState


_POS = Position(x=0, y=0)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_canvas(nodes, edges=None):
    return CanvasState(nodes=nodes, edges=edges or [])


def make_result_node(node_id="result", fmt="csv", limit=None, geom_col=None):
    return Node(id=node_id, type="result", position=_POS,
                fileName="output", format=fmt, limit=limit, geomColumn=geom_col)


def make_source_node(node_id="src", table_name="tab"):
    return Node(id=node_id, type="source", position=_POS,
                table_name=table_name,
                columns=[Column(name="id", type="int", visible=True)])


def make_feature_node(node_id="feat", layer_url="http://x/0",
                      geometry_type="esriGeometryPoint"):
    return Node(id=node_id, type="feature_source", position=_POS,
                layer_url=layer_url, geometry_type=geometry_type,
                columns=[Column(name="id", type="int", visible=True)])


def make_executor(features_service=None):
    return EstraiExecutor(
        project_service=MagicMock(),
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        features_service=features_service,
    )


# =============================================================================
# _resolve_connection
# =============================================================================

class TestResolveConnection:

    def test_db_connection_trovata(self):
        ex = make_executor()
        db_conn = MagicMock()
        ex.connection_service.get_connection.return_value = db_conn
        conn, conn_type = ex._resolve_connection("conn-1")
        assert conn is db_conn
        assert conn_type == "db"

    def test_arcgis_connection_trovata(self):
        features_service = MagicMock()
        arc_conn = MagicMock()
        ex = make_executor(features_service=features_service)
        ex.connection_service.get_connection.return_value = None  # non DB
        features_service.get_connection.return_value = arc_conn
        conn, conn_type = ex._resolve_connection("arc-1")
        assert conn is arc_conn
        assert conn_type == "arcgis"

    def test_nessuna_connessione_raise(self):
        """Mutation target: connessione non trovata → ValueError."""
        features_service = MagicMock()
        ex = make_executor(features_service=features_service)
        ex.connection_service.get_connection.return_value = None
        features_service.get_connection.return_value = None
        with pytest.raises(ValueError, match="non trovata"):
            ex._resolve_connection("conn-inesistente")

    def test_senza_features_service_raise(self):
        """Senza features_service, la connessione ArcGIS non viene cercata."""
        ex = make_executor(features_service=None)
        ex.connection_service.get_connection.return_value = None
        with pytest.raises(ValueError):
            ex._resolve_connection("conn-inesistente")


# =============================================================================
# execute() — branch di errore
# =============================================================================

class TestExecuteErrorBranches:

    def test_no_result_node(self):
        """Canvas senza nodo result → success=False."""
        ex = make_executor()
        canvas = make_canvas([make_source_node()])
        result = ex.execute("proj-1", canvas)
        assert result["success"] is False
        assert "risultato" in result["message"].lower()

    def test_no_metadata(self):
        """Progetto senza metadata → success=False."""
        ex = make_executor()
        ex.project_service.get_project_metadata.return_value = None
        canvas = make_canvas([make_result_node()])
        result = ex.execute("proj-1", canvas)
        assert result["success"] is False

    def test_no_connection_id(self):
        """Metadata senza default_connection_id → success=False."""
        ex = make_executor()
        meta = MagicMock()
        meta.default_connection_id = None
        ex.project_service.get_project_metadata.return_value = meta
        canvas = make_canvas([make_result_node()])
        result = ex.execute("proj-1", canvas)
        assert result["success"] is False

    def test_connessione_non_trovata(self):
        """_resolve_connection lancia ValueError → success=False."""
        ex = make_executor()
        meta = MagicMock()
        meta.default_connection_id = "conn-x"
        ex.project_service.get_project_metadata.return_value = meta
        ex.connection_service.get_connection.return_value = None
        canvas = make_canvas([make_result_node()])
        result = ex.execute("proj-1", canvas)
        assert result["success"] is False

    def test_percorso_arcgis(self):
        """Con connessione ArcGIS → chiama _execute_feature_service."""
        features_service = MagicMock()
        ex = make_executor(features_service=features_service)
        meta = MagicMock()
        meta.default_connection_id = "arc-1"
        ex.project_service.get_project_metadata.return_value = meta
        ex.connection_service.get_connection.return_value = None
        arc_conn = MagicMock()
        arc_conn.id = "arc-1"
        features_service.get_connection.return_value = arc_conn
        features_service.execute_query.return_value = {
            "success": True, "columns": ["id"], "rows_count": 0
        }

        feat_node = make_feature_node()
        canvas = make_canvas([feat_node, make_result_node()])
        result = ex.execute("proj-1", canvas)
        features_service.execute_query.assert_called_once()


# =============================================================================
# _execute_feature_service()
# =============================================================================

class TestExecuteFeatureService:

    def test_senza_feature_source_node(self):
        """Canvas senza feature_source → success=False."""
        features_service = MagicMock()
        ex = make_executor(features_service=features_service)
        canvas = make_canvas([make_source_node(), make_result_node()])
        result = ex._execute_feature_service(
            "proj-1", canvas, make_result_node(),
            MagicMock(id="arc-1")
        )
        assert result["success"] is False
        assert "Feature Source" in result["message"]

    def test_con_feature_source_node_success(self):
        """Con feature_source → chiama features_service.execute_query."""
        features_service = MagicMock()
        features_service.execute_query.return_value = {
            "success": True, "columns": ["id"], "rows_count": 3
        }
        ex = make_executor(features_service=features_service)
        ex.project_service.update_project_columns_metadata = MagicMock()

        feat_node = make_feature_node()
        result_node = make_result_node()
        canvas = make_canvas([feat_node, result_node])

        result = ex._execute_feature_service(
            "proj-1", canvas, result_node, MagicMock(id="arc-1")
        )
        assert result["success"] is True
        assert "ArcGIS REST" in result["query"]
        features_service.execute_query.assert_called_once()

    def test_con_limit_sul_result_node(self):
        """Limit sul result node → passato a features_service."""
        features_service = MagicMock()
        features_service.execute_query.return_value = {"success": True, "columns": []}
        ex = make_executor(features_service=features_service)
        ex.project_service.update_project_columns_metadata = MagicMock()

        feat_node = make_feature_node()
        result_node = make_result_node(limit=100)
        canvas = make_canvas([feat_node, result_node])

        ex._execute_feature_service("proj-1", canvas, result_node, MagicMock(id="arc-1"))
        call_kwargs = features_service.execute_query.call_args[1]
        assert call_kwargs.get("max_features") == 100

    def test_eccezione_gestita(self):
        """Eccezione in features_service → success=False, non crash."""
        features_service = MagicMock()
        features_service.execute_query.side_effect = RuntimeError("timeout")
        ex = make_executor(features_service=features_service)

        feat_node = make_feature_node()
        result_node = make_result_node()
        canvas = make_canvas([feat_node, result_node])

        result = ex._execute_feature_service(
            "proj-1", canvas, result_node, MagicMock(id="arc-1")
        )
        assert result["success"] is False
        assert "timeout" in result["message"]


# =============================================================================
# preview() — branch di errore
# =============================================================================

class TestPreviewErrorBranches:

    def test_no_result_node(self):
        ex = make_executor()
        canvas = make_canvas([make_source_node()])
        result = ex.preview("proj-1", canvas, limit=10)
        assert result["success"] is False

    def test_no_metadata(self):
        ex = make_executor()
        ex.project_service.get_project_metadata.return_value = None
        canvas = make_canvas([make_result_node()])
        result = ex.preview("proj-1", canvas, limit=10)
        assert result["success"] is False

    def test_connessione_non_trovata(self):
        ex = make_executor()
        meta = MagicMock()
        meta.default_connection_id = "conn-x"
        ex.project_service.get_project_metadata.return_value = meta
        ex.connection_service.get_connection.return_value = None
        canvas = make_canvas([make_result_node()])
        result = ex.preview("proj-1", canvas, limit=10)
        assert result["success"] is False

    def test_percorso_arcgis_preview(self):
        """Con ArcGIS → chiama _preview_feature_service."""
        features_service = MagicMock()
        features_service.query_layer.return_value = (
            [{"id": 1}, {"id": 2}], ["id"]
        )
        ex = make_executor(features_service=features_service)
        meta = MagicMock()
        meta.default_connection_id = "arc-1"
        ex.project_service.get_project_metadata.return_value = meta
        ex.connection_service.get_connection.return_value = None
        arc_conn = MagicMock()
        arc_conn.id = "arc-1"
        features_service.get_connection.return_value = arc_conn

        feat_node = make_feature_node()
        canvas = make_canvas([feat_node, make_result_node()])
        result = ex.preview("proj-1", canvas, limit=10)
        features_service.query_layer.assert_called_once()


# =============================================================================
# _preview_feature_service()
# =============================================================================

class TestPreviewFeatureService:

    def test_senza_feature_source_node(self):
        features_service = MagicMock()
        ex = make_executor(features_service=features_service)
        canvas = make_canvas([make_source_node(), make_result_node()])
        result = ex._preview_feature_service(
            "proj-1", canvas, make_result_node(),
            MagicMock(id="arc-1"), limit=10
        )
        assert result["success"] is False

    def test_preview_con_feature_source(self):
        features_service = MagicMock()
        features_service.query_layer.return_value = (
            [{"id": 1, "nome": "A"}, {"id": 2, "nome": "B"}], ["id", "nome"]
        )
        ex = make_executor(features_service=features_service)

        feat_node = make_feature_node()
        result_node = make_result_node()
        canvas = make_canvas([feat_node, result_node])

        result = ex._preview_feature_service(
            "proj-1", canvas, result_node, MagicMock(id="arc-1"), limit=10
        )
        assert result["success"] is True
        assert len(result["rows"]) == 2
        assert result["truncated"] is False

    def test_preview_troncamento(self):
        """Se rows > limit → truncated=True, righe troncate a limit."""
        features_service = MagicMock()
        # Restituisce limit+1 righe → troncamento
        features_service.query_layer.return_value = (
            [{"id": i} for i in range(6)], ["id"]  # 6 righe, limit=5
        )
        ex = make_executor(features_service=features_service)

        feat_node = make_feature_node()
        result_node = make_result_node()
        canvas = make_canvas([feat_node, result_node])

        result = ex._preview_feature_service(
            "proj-1", canvas, result_node, MagicMock(id="arc-1"), limit=5
        )
        assert result["success"] is True
        assert result["truncated"] is True
        assert len(result["rows"]) == 5

    def test_eccezione_gestita(self):
        features_service = MagicMock()
        features_service.query_layer.side_effect = ConnectionError("timeout ArcGIS")
        ex = make_executor(features_service=features_service)

        feat_node = make_feature_node()
        canvas = make_canvas([feat_node, make_result_node()])

        result = ex._preview_feature_service(
            "proj-1", canvas, make_result_node(), MagicMock(id="arc-1"), limit=10
        )
        assert result["success"] is False
        assert "timeout" in result["message"]


# =============================================================================
# _salva_tipi_colonne_da_canvas()
# =============================================================================

class TestSalvaTipiColonne:

    def test_salva_tipi_corretti(self):
        ex = make_executor()
        ex.project_service.update_project_columns_metadata = MagicMock()

        src = Node(id="s1", type="source", position=_POS, table_name="tab",
                   columns=[
                       Column(name="id",   type="int",  visible=True),
                       Column(name="nome", type="str",  visible=True),
                   ])
        canvas = CanvasState(nodes=[src], edges=[])
        res = {"success": True, "columns": ["id", "nome"]}

        ex._salva_tipi_colonne_da_canvas("proj-1", canvas, res)

        ex.project_service.update_project_columns_metadata.assert_called_once()
        cols_saved = ex.project_service.update_project_columns_metadata.call_args[0][1]
        types = {c["name"]: c["type"] for c in cols_saved}
        assert types["id"]   == "int"
        assert types["nome"] == "str"

    def test_colonne_vuote_non_crasha(self):
        """Se res non ha columns → non deve crashare."""
        ex = make_executor()
        ex.project_service.update_project_columns_metadata = MagicMock()
        canvas = CanvasState(nodes=[], edges=[])
        res = {"success": True, "columns": []}
        ex._salva_tipi_colonne_da_canvas("proj-1", canvas, res)

    def test_errore_update_non_propaga(self):
        """Eccezione in update_project_columns_metadata → warning, non crash."""
        ex = make_executor()
        ex.project_service.update_project_columns_metadata.side_effect = RuntimeError("DB error")

        src = Node(id="s1", type="source", position=_POS, table_name="tab",
                   columns=[Column(name="id", type="int", visible=True)])
        canvas = CanvasState(nodes=[src], edges=[])
        res = {"success": True, "columns": ["id"]}

        # Non deve propagare l'eccezione
        ex._salva_tipi_colonne_da_canvas("proj-1", canvas, res)
