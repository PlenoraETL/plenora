"""
tests/estrai/unit/test_executor_router_coverage_part2.py
========================================================
Test coverage per executor.py e query_executor.py (parte 2):
  - _execute_database, _preview_database
  - _costruisci_sql_preview, _build_canvas_type_map
  - QueryExecutor timeout, dispose, execute_query pipeline
  - create_executor factory, result_store import fallback
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

from app.core.estrai.executor import EstraiExecutor
from app.shared.models import Node, Column, Edge, Position, CanvasState


_POS = Position(x=0, y=0)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_canvas(nodes, edges=None):
    return CanvasState(nodes=nodes, edges=edges or [])


def make_executor(features_service=None):
    return EstraiExecutor(
        project_service=MagicMock(),
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        features_service=features_service,
    )


# =============================================================================
# EXECUTOR.PY — _execute_database (lines 205-241)
# =============================================================================

class TestExecuteDatabase:

    def test_execute_database_sql_gen_error(self):
        """Errore generazione SQL -> errore gestito."""
        ex = make_executor()
        conn = MagicMock(type="postgres", db_schema=None)
        result_node = Node(id="r1", type="result", position=_POS)
        canvas = make_canvas([result_node])
        ex.generator.generate_sql.side_effect = RuntimeError("parse fail")

        res = ex._execute_database("proj-1", canvas, result_node, conn)
        assert res["success"] is False
        assert "parse fail" in res["message"]

    def test_execute_database_success_path(self):
        """Percorso successo: genera SQL, esegue, salva tipi."""
        ex = make_executor()
        conn = MagicMock(type="postgres", db_schema="public")
        result_node = Node(
            id="r1", type="result", position=_POS,
            fileName="output", format="csv", geomColumn="t.geom",
        )
        source_node = Node(
            id="s1", type="source", position=_POS, table_name="tab",
            columns=[Column(name="id", type="int", visible=True)],
        )
        canvas = make_canvas([source_node, result_node])
        metadata = MagicMock(group_id="g1")

        ex.generator.generate_sql.return_value = {"sql": "SELECT id FROM tab", "params": {}}
        ex.executor.execute_query.return_value = {
            "success": True, "columns": ["id"], "rows_count": 5,
        }

        res = ex._execute_database("proj-1", canvas, result_node, conn, metadata, run_id="run1")
        assert res["success"] is True
        assert res["query"] == "SELECT id FROM tab"
        call_kw = ex.executor.execute_query.call_args
        assert call_kw.kwargs["geom_column"] == "geom"

    def test_execute_database_failure_translates_error(self):
        """Errore esecuzione -> traduce messaggio + user_error flag."""
        ex = make_executor()
        conn = MagicMock(type="postgres", db_schema=None)
        result_node = Node(id="r1", type="result", position=_POS)
        canvas = make_canvas([result_node])

        ex.generator.generate_sql.return_value = {"sql": "SELECT 1", "params": {}}
        ex.executor.execute_query.return_value = {
            "success": False,
            "message": "operator does not exist: integer = text",
        }

        res = ex._execute_database("proj-1", canvas, result_node, conn)
        assert res["success"] is False
        assert "Tipi incompatibili" in res["message"]
        assert res.get("user_error") is True


# =============================================================================
# EXECUTOR.PY — preview database path
# =============================================================================

class TestPreviewDatabase:

    def test_preview_database_success(self):
        ex = make_executor()
        conn = MagicMock(type="postgres", db_schema=None)
        result_node = Node(id="r1", type="result", position=_POS)
        source_node = Node(
            id="s1", type="source", position=_POS, table_name="tab",
            columns=[Column(name="id", type="int", visible=True)],
        )
        canvas = make_canvas([source_node, result_node])

        ex.generator.generate_sql.return_value = {"sql": "SELECT id FROM tab", "params": {}}
        ex.executor.execute_raw.return_value = ([(1,), (2,)], ["id"])

        res = ex._preview_database("proj-1", canvas, result_node, conn, limit=10)
        assert res["success"] is True
        assert res["total_count"] == 2
        assert len(res["rows"]) == 2

    def test_preview_database_truncated(self):
        """Piu righe di limit -> truncated=True."""
        ex = make_executor()
        conn = MagicMock(type="postgres", db_schema=None)
        result_node = Node(id="r1", type="result", position=_POS)
        canvas = make_canvas([result_node])

        ex.generator.generate_sql.return_value = {"sql": "SELECT 1", "params": {}}
        # limit=2, restituisce 3 righe (limit+1)
        ex.executor.execute_raw.return_value = ([(1,), (2,), (3,)], ["val"])

        res = ex._preview_database("proj-1", canvas, result_node, conn, limit=2)
        assert res["success"] is True
        assert res["truncated"] is True
        assert res["preview_count"] == 2

    def test_preview_database_data_error(self):
        """SqlAlchemyDataError -> user_error=True."""
        from sqlalchemy.exc import DataError as RealDataError
        ex = make_executor()
        conn = MagicMock(type="postgres", db_schema=None)
        result_node = Node(id="r1", type="result", position=_POS)
        canvas = make_canvas([result_node])

        ex.generator.generate_sql.return_value = {"sql": "SELECT 1", "params": {}}
        ex.executor.execute_raw.side_effect = RealDataError(
            "statement", {}, Exception("invalid input syntax for type integer")
        )

        res = ex._preview_database("proj-1", canvas, result_node, conn, limit=10)
        assert res["success"] is False
        assert res.get("user_error") is True

    def test_preview_database_programming_error(self):
        """SqlAlchemyProgrammingError -> user_error=True."""
        from sqlalchemy.exc import ProgrammingError as RealPE
        ex = make_executor()
        conn = MagicMock(type="postgres", db_schema=None)
        result_node = Node(id="r1", type="result", position=_POS)
        canvas = make_canvas([result_node])

        ex.generator.generate_sql.return_value = {"sql": "SELECT 1", "params": {}}
        ex.executor.execute_raw.side_effect = RealPE(
            "statement", {}, Exception("undefinedfunction: trim(bigint)")
        )

        res = ex._preview_database("proj-1", canvas, result_node, conn, limit=10)
        assert res["success"] is False
        assert res.get("user_error") is True

    def test_preview_database_generic_error(self):
        """Eccezione generica -> errore senza user_error."""
        ex = make_executor()
        conn = MagicMock(type="postgres", db_schema=None)
        result_node = Node(id="r1", type="result", position=_POS)
        canvas = make_canvas([result_node])

        ex.generator.generate_sql.return_value = {"sql": "SELECT 1", "params": {}}
        ex.executor.execute_raw.side_effect = RuntimeError("timeout")

        res = ex._preview_database("proj-1", canvas, result_node, conn, limit=10)
        assert res["success"] is False
        assert "user_error" not in res


# =============================================================================
# EXECUTOR.PY — execute() e preview() full path (con _resolve_connection)
# =============================================================================

class TestExecuteFullPath:

    def test_execute_no_result_node(self):
        ex = make_executor()
        canvas = make_canvas([Node(id="s1", type="source", position=_POS, table_name="t")])
        res = ex.execute("proj-1", canvas)
        assert res["success"] is False
        assert "risultato" in res["message"].lower()

    def test_execute_no_connection_id(self):
        ex = make_executor()
        ex.project_service.get_project_metadata.return_value = MagicMock(default_connection_id=None)
        result_node = Node(id="r1", type="result", position=_POS)
        canvas = make_canvas([result_node])
        res = ex.execute("proj-1", canvas)
        assert res["success"] is False

    def test_execute_connection_not_found(self):
        ex = make_executor()
        ex.project_service.get_project_metadata.return_value = MagicMock(default_connection_id="c1")
        ex.connection_service.get_connection.return_value = None
        result_node = Node(id="r1", type="result", position=_POS)
        canvas = make_canvas([result_node])
        res = ex.execute("proj-1", canvas)
        assert res["success"] is False
        assert "non trovata" in res["message"]

    def test_execute_db_path(self):
        """execute() con connessione DB -> chiama _execute_database."""
        ex = make_executor()
        ex.project_service.get_project_metadata.return_value = MagicMock(default_connection_id="c1")
        conn = MagicMock(type="postgres", db_schema=None)
        ex.connection_service.get_connection.return_value = conn
        result_node = Node(id="r1", type="result", position=_POS)
        canvas = make_canvas([result_node])

        ex.generator.generate_sql.return_value = {"sql": "SELECT 1", "params": {}}
        ex.executor.execute_query.return_value = {"success": True, "columns": ["x"], "rows_count": 1}

        res = ex.execute("proj-1", canvas)
        assert res["success"] is True

    def test_preview_db_path(self):
        """preview() con connessione DB -> chiama _preview_database."""
        ex = make_executor()
        ex.project_service.get_project_metadata.return_value = MagicMock(default_connection_id="c1")
        conn = MagicMock(type="postgres", db_schema=None)
        ex.connection_service.get_connection.return_value = conn
        result_node = Node(id="r1", type="result", position=_POS)
        canvas = make_canvas([result_node])

        ex.generator.generate_sql.return_value = {"sql": "SELECT 1", "params": {}}
        ex.executor.execute_raw.return_value = ([(1,)], ["x"])

        res = ex.preview("proj-1", canvas, limit=10)
        assert res["success"] is True


# =============================================================================
# EXECUTOR.PY — _costruisci_sql_preview_only branches
# =============================================================================

class TestCostruisciSqlPreviewOnly:

    def test_mssql_branch(self):
        ex = make_executor()
        sql = ex._costruisci_sql_preview_only("SELECT * FROM t", 10, "mssql")
        assert "TOP 11" in sql

    def test_oracle_branch(self):
        ex = make_executor()
        sql = ex._costruisci_sql_preview_only("SELECT * FROM t", 5, "oracle")
        assert "FETCH FIRST 6 ROWS ONLY" in sql

    def test_unsupported_db_raises(self):
        ex = make_executor()
        with pytest.raises(ValueError, match="non supportato"):
            ex._costruisci_sql_preview_only("SELECT 1", 10, "mongodb")


# =============================================================================
# EXECUTOR.PY — _costruisci_sql_preview (con count) branches
# =============================================================================

class TestCostruisciSqlPreviewBranches:

    def test_mysql_branch(self):
        ex = make_executor()
        preview, count = ex._costruisci_sql_preview("SELECT * FROM t", 10, "mysql")
        assert "LIMIT 11" in preview
        assert "AS subquery" in count

    def test_sqlite_branch(self):
        ex = make_executor()
        preview, count = ex._costruisci_sql_preview("SELECT * FROM t", 5, "sqlite")
        assert "LIMIT 6" in preview
        assert "COUNT(*)" in count

    def test_mssql_branch(self):
        ex = make_executor()
        preview, count = ex._costruisci_sql_preview("SELECT * FROM t", 10, "mssql")
        assert "TOP 11" in preview

    def test_oracle_branch(self):
        ex = make_executor()
        preview, count = ex._costruisci_sql_preview("SELECT * FROM t", 5, "oracle")
        assert "FETCH FIRST 6 ROWS ONLY" in preview

    def test_unsupported_db_raises(self):
        ex = make_executor()
        with pytest.raises(ValueError, match="non supportato"):
            ex._costruisci_sql_preview("SELECT 1", 10, "mongodb")


# =============================================================================
# EXECUTOR.PY — _normalize_geometry_type
# =============================================================================

class TestNormalizeGeometryType:

    def test_already_normalized(self):
        ex = make_executor()
        assert ex._normalize_geometry_type("geometry:polygon") == "geometry:polygon"

    def test_esri_format(self):
        ex = make_executor()
        assert ex._normalize_geometry_type("esriGeometryPolygon") == "geometry:polygon"
        assert ex._normalize_geometry_type("esriGeometryPoint") == "geometry:point"
        assert ex._normalize_geometry_type("esriGeometryPolyline") == "geometry:line"

    def test_generic_format(self):
        ex = make_executor()
        assert ex._normalize_geometry_type("Point") == "geometry:point"
        assert ex._normalize_geometry_type("MultiPolygon") == "geometry:multipolygon"
        assert ex._normalize_geometry_type("LineString") == "geometry:line"

    def test_empty_returns_geometry(self):
        ex = make_executor()
        assert ex._normalize_geometry_type("") == "geometry"

    def test_unknown_returns_geometry(self):
        ex = make_executor()
        assert ex._normalize_geometry_type("UnknownType") == "geometry"


# =============================================================================
# EXECUTOR.PY — _rimuovi_limit
# =============================================================================

class TestRimuoviLimit:

    def test_remove_limit(self):
        ex = make_executor()
        assert "LIMIT" not in ex._rimuovi_limit("SELECT * FROM t LIMIT 100")

    def test_remove_top(self):
        ex = make_executor()
        result = ex._rimuovi_limit("SELECT TOP 100 * FROM t")
        assert "TOP" not in result
        assert "SELECT" in result

    def test_remove_fetch_first(self):
        ex = make_executor()
        result = ex._rimuovi_limit("SELECT * FROM t FETCH FIRST 100 ROWS ONLY")
        assert "FETCH" not in result


# =============================================================================
# EXECUTOR.PY — _salva_tipi_colonne_da_canvas
# =============================================================================

class TestSalvaTipiColonne:

    def test_salva_tipi_success(self):
        ex = make_executor()
        source = Node(
            id="s1", type="source", position=_POS, table_name="t",
            columns=[Column(name="id", type="int", visible=True)],
        )
        canvas = make_canvas([source])
        res = {"success": True, "columns": ["id"]}

        ex._salva_tipi_colonne_da_canvas("proj-1", canvas, res)

        ex.project_service.update_project_columns_metadata.assert_called_once()
        assert res["columns_metadata"] == [{"name": "id", "type": "int"}]

    def test_salva_tipi_exception_non_blocking(self):
        ex = make_executor()
        ex.project_service.update_project_columns_metadata.side_effect = RuntimeError("fail")
        canvas = make_canvas([])
        res = {"success": True, "columns": ["x"]}

        # Non deve crashare
        ex._salva_tipi_colonne_da_canvas("proj-1", canvas, res)


# =============================================================================
# EXECUTOR.PY — _get_visible_columns e _is_column_visible
# =============================================================================

class TestVisibleColumns:

    def test_get_visible_columns(self):
        ex = make_executor()
        node = Node(
            id="fs1", type="feature_source", position=_POS,
            columns=[
                Column(name="name", type="str", visible=True),
                Column(name="hidden", type="str", visible=False),
                Column(name="geo", type="geometry", visible=True),
            ],
        )
        visible = ex._get_visible_columns(node)
        assert "name" in visible
        assert "geo" in visible
        assert "hidden" not in visible

    def test_get_visible_columns_empty(self):
        ex = make_executor()
        node = Node(id="s1", type="source", position=_POS, table_name="t")
        assert ex._get_visible_columns(node) == []

    def test_is_column_visible_true(self):
        ex = make_executor()
        node = Node(
            id="s1", type="source", position=_POS, table_name="t",
            columns=[Column(name="id", type="int", visible=True)],
        )
        assert ex._is_column_visible(node, "id") is True

    def test_is_column_visible_false(self):
        ex = make_executor()
        node = Node(
            id="s1", type="source", position=_POS, table_name="t",
            columns=[Column(name="secret", type="str", visible=False)],
        )
        assert ex._is_column_visible(node, "secret") is False

    def test_is_column_visible_not_found_default_true(self):
        ex = make_executor()
        node = Node(
            id="s1", type="source", position=_POS, table_name="t",
            columns=[Column(name="id", type="int", visible=True)],
        )
        assert ex._is_column_visible(node, "nonexistent") is True
