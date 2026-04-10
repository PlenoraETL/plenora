"""
tests/carica/unit/test_router_coverage.py
==========================================
Unit tests for app.core.carica.router — targeting uncovered lines.

Covers:
  - init_carica_service
  - _get_connection helper (404, 503)
  - _create_engine helper (factory path, fallback for each db_type)
  - GET /carica/schemas/{connection_id}
  - GET /carica/tables/{connection_id}/{schema}
  - GET /carica/columns/{connection_id}/{schema}/{table}
  - POST /carica/auto-mapping
  - POST /carica/validate
  - POST /carica/check-conflicts (all branches)
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch, PropertyMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

FAKE_CONN_DICT = {
    "type": "postgres",
    "host": "localhost",
    "port": 5432,
    "database": "testdb",
    "username": "user",
    "password": "pass",
}


def _fake_current_user():
    """Dependency override for get_current_user."""
    from app.core.auth.auth import CurrentUser
    return CurrentUser(user_id="u1", username="tester", group_id="g1", role="admin")


@pytest.fixture()
def client():
    """TestClient with auth dependency overridden and _connection_service set."""
    import app.core.carica.router as mod
    from app.core.auth.auth import get_current_user

    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[get_current_user] = _fake_current_user

    # Prepare a mock connection service
    mock_conn_svc = MagicMock()
    mock_conn_obj = MagicMock()
    mock_conn_obj.model_dump.return_value = dict(FAKE_CONN_DICT)
    mock_conn_svc.get_connection.return_value = mock_conn_obj

    old_svc = mod._connection_service
    old_exec = mod._executor
    mod._connection_service = mock_conn_svc
    mod._executor = MagicMock()

    yield TestClient(app, raise_server_exceptions=False)

    mod._connection_service = old_svc
    mod._executor = old_exec


@pytest.fixture()
def client_no_service():
    """TestClient where _connection_service is None (503 path)."""
    import app.core.carica.router as mod
    from app.core.auth.auth import get_current_user

    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[get_current_user] = _fake_current_user

    old_svc = mod._connection_service
    old_exec = mod._executor
    mod._connection_service = None
    mod._executor = None

    yield TestClient(app, raise_server_exceptions=False)

    mod._connection_service = old_svc
    mod._executor = old_exec




# ============================================================================
# init_carica_service  (lines 57-58)
# ============================================================================

class TestInitCaricaService:

    def test_sets_globals(self):
        import app.core.carica.router as mod

        old_svc = mod._connection_service
        old_exec = mod._executor
        try:
            mock_svc = MagicMock()
            mod.init_carica_service(mock_svc)
            assert mod._connection_service is mock_svc
            assert mod._executor is not None
        finally:
            mod._connection_service = old_svc
            mod._executor = old_exec


# ============================================================================
# _get_connection  (lines 456-465)
# ============================================================================

class TestGetConnection:

    def test_connection_not_found_raises_404(self):
        import app.core.carica.router as mod
        from fastapi import HTTPException

        mock_svc = MagicMock()
        mock_svc.get_connection.return_value = None

        old = mod._connection_service
        try:
            mod._connection_service = mock_svc
            with pytest.raises(HTTPException) as exc_info:
                mod._get_connection("missing-id")
            assert exc_info.value.status_code == 404
        finally:
            mod._connection_service = old

    def test_no_service_raises_503(self):
        import app.core.carica.router as mod
        from fastapi import HTTPException

        old = mod._connection_service
        try:
            mod._connection_service = None
            with pytest.raises(HTTPException) as exc_info:
                mod._get_connection("any-id")
            assert exc_info.value.status_code == 503
        finally:
            mod._connection_service = old

    def test_found_returns_dict(self):
        import app.core.carica.router as mod

        mock_svc = MagicMock()
        mock_conn = MagicMock()
        mock_conn.model_dump.return_value = {"type": "postgres"}
        mock_svc.get_connection.return_value = mock_conn

        old = mod._connection_service
        try:
            mod._connection_service = mock_svc
            result = mod._get_connection("conn-1")
            assert result == {"type": "postgres"}
        finally:
            mod._connection_service = old


# ============================================================================
# _create_engine  (lines 473-500)
# ============================================================================

class TestCreateEngine:

    @patch("app.core.carica.router.DefaultEngineFactory", create=True)
    @patch("app.core.carica.router._dict_to_conn_obj")
    def test_factory_path(self, mock_d2c, mock_factory_cls):
        """When DefaultEngineFactory is importable, use it."""
        import app.core.carica.router as mod

        mock_engine = MagicMock()
        mock_factory_cls.return_value.create_engine.return_value = mock_engine
        mock_d2c.return_value = "conn_obj"

        # Patch the local import inside _create_engine
        with patch("app.db.query_executor.DefaultEngineFactory", mock_factory_cls):
            result = mod._create_engine(FAKE_CONN_DICT)

        assert result is mock_engine

    def test_fallback_postgres(self):
        import app.core.carica.router as mod

        conn = dict(FAKE_CONN_DICT, type="postgres")
        with patch("app.db.query_executor.DefaultEngineFactory", side_effect=ImportError):
            with patch("app.core.carica.router.create_engine", create=True) as mock_ce:
                # Force ImportError on the factory import so fallback runs
                # We need to patch the import inside _create_engine
                pass

        # Use a more direct approach: patch the whole function path
        with patch.dict("sys.modules", {"app.db.query_executor": None}):
            with patch("sqlalchemy.create_engine") as mock_ce:
                mock_ce.return_value = MagicMock()
                result = mod._create_engine(conn)
                call_args = mock_ce.call_args[0][0]
                assert "postgresql://" in call_args

    def test_fallback_mysql(self):
        import app.core.carica.router as mod
        conn = dict(FAKE_CONN_DICT, type="mysql")
        with patch.dict("sys.modules", {"app.db.query_executor": None}):
            with patch("sqlalchemy.create_engine") as mock_ce:
                mock_ce.return_value = MagicMock()
                result = mod._create_engine(conn)
                call_args = mock_ce.call_args[0][0]
                assert "mysql+pymysql://" in call_args

    def test_fallback_mariadb(self):
        import app.core.carica.router as mod
        conn = dict(FAKE_CONN_DICT, type="mariadb")
        with patch.dict("sys.modules", {"app.db.query_executor": None}):
            with patch("sqlalchemy.create_engine") as mock_ce:
                mock_ce.return_value = MagicMock()
                result = mod._create_engine(conn)
                call_args = mock_ce.call_args[0][0]
                assert "mysql+pymysql://" in call_args

    def test_fallback_mssql(self):
        import app.core.carica.router as mod
        conn = dict(FAKE_CONN_DICT, type="mssql")
        with patch.dict("sys.modules", {"app.db.query_executor": None}):
            with patch("sqlalchemy.create_engine") as mock_ce:
                mock_ce.return_value = MagicMock()
                result = mod._create_engine(conn)
                call_args = mock_ce.call_args[0][0]
                assert "mssql+pyodbc://" in call_args

    def test_fallback_oracle(self):
        import app.core.carica.router as mod
        conn = dict(FAKE_CONN_DICT, type="oracle")
        with patch.dict("sys.modules", {"app.db.query_executor": None}):
            with patch("sqlalchemy.create_engine") as mock_ce:
                mock_ce.return_value = MagicMock()
                result = mod._create_engine(conn)
                call_args = mock_ce.call_args[0][0]
                assert "oracle+oracledb://" in call_args

    def test_fallback_unsupported_raises(self):
        import app.core.carica.router as mod
        conn = dict(FAKE_CONN_DICT, type="sqlite_unsupported")
        with patch.dict("sys.modules", {"app.db.query_executor": None}):
            with patch("sqlalchemy.create_engine") as mock_ce:
                with pytest.raises(ValueError, match="non supportato"):
                    mod._create_engine(conn)


# ============================================================================
# GET /carica/schemas/{connection_id}  (lines 132-145)
# ============================================================================

class TestGetSchemas:

    def test_success(self, client):
        with patch("app.core.carica.router._create_engine") as mock_eng, \
             patch("app.core.carica.router.DatabaseWriter") as mock_dw:
            mock_dw.return_value.get_schemas.return_value = ["public", "geo"]
            res = client.get("/carica/schemas/conn-1")
        assert res.status_code == 200
        data = res.json()
        assert data["schemas"] == ["public", "geo"]
        assert data["db_type"] == "postgres"

    def test_exception_returns_500(self, client):
        with patch("app.core.carica.router._get_connection", side_effect=RuntimeError("boom")):
            res = client.get("/carica/schemas/conn-1")
        assert res.status_code == 500

    def test_no_service_returns_500(self, client_no_service):
        """Without service, _get_connection raises → catch-all returns 500."""
        res = client_no_service.get("/carica/schemas/conn-1")
        assert res.status_code == 500


# ============================================================================
# GET /carica/tables/{connection_id}/{schema}  (lines 158-171)
# ============================================================================

class TestGetTables:

    def test_success(self, client):
        with patch("app.core.carica.router._create_engine"), \
             patch("app.core.carica.router.DatabaseWriter") as mock_dw:
            mock_dw.return_value.get_tables.return_value = ["users", "orders"]
            res = client.get("/carica/tables/conn-1/public")
        assert res.status_code == 200
        data = res.json()
        assert data["tables"] == ["users", "orders"]
        assert data["schema_name"] == "public"

    def test_exception_returns_500(self, client):
        with patch("app.core.carica.router._get_connection", side_effect=RuntimeError("boom")):
            res = client.get("/carica/tables/conn-1/public")
        assert res.status_code == 500


# ============================================================================
# GET /carica/columns/{connection_id}/{schema}/{table}  (lines 185-206)
# ============================================================================

class TestGetColumns:

    def test_success_existing_table(self, client):
        cols = [{"name": "id", "type": "integer"}, {"name": "name", "type": "varchar"}]
        with patch("app.core.carica.router._create_engine"), \
             patch("app.core.carica.router.DatabaseWriter") as mock_dw:
            mock_dw.return_value.get_existing_columns.return_value = cols
            res = client.get("/carica/columns/conn-1/public/users")
        assert res.status_code == 200
        data = res.json()
        assert data["columns"] == cols
        assert data["table_name"] == "users"
        assert data["table_exists"] is True

    def test_success_nonexistent_table(self, client):
        with patch("app.core.carica.router._create_engine"), \
             patch("app.core.carica.router.DatabaseWriter") as mock_dw:
            mock_dw.return_value.get_existing_columns.return_value = []
            res = client.get("/carica/columns/conn-1/public/nope")
        assert res.status_code == 200
        data = res.json()
        assert data["table_exists"] is False

    def test_exception_returns_500(self, client):
        with patch("app.core.carica.router._get_connection", side_effect=RuntimeError("boom")):
            res = client.get("/carica/columns/conn-1/public/users")
        assert res.status_code == 500


# ============================================================================
# POST /carica/auto-mapping  (lines 221-302)
# ============================================================================

class TestAutoMapping:

    def _base_payload(self, **overrides):
        payload = {
            "connection_id": "conn-1",
            "schema_name": "public",
            "table_name": "",
            "project_id": "",
            "srid": 4326,
            "source_columns": [
                {"name": "id", "type": "integer"},
                {"name": "name", "type": "str"},
                {"name": "value", "type": "float"},
                {"name": "flag", "type": "boolean"},
                {"name": "geom", "type": "geometry"},
            ],
        }
        payload.update(overrides)
        return payload

    def test_success_no_table(self, client):
        """Auto-mapping with no existing table: empty existing_columns."""
        mock_mapping = MagicMock(
            source_column="id", dest_column="id", dest_type="INTEGER",
            is_geometry=False, srid=4326, is_primary_key=False, skip=False,
        )
        with patch("app.core.carica.router._get_connection", return_value=FAKE_CONN_DICT), \
             patch("app.core.carica.router.ColumnMapper") as mock_cm:
            mock_cm.return_value.auto_map.return_value = [mock_mapping]
            mock_cm.return_value.suggest_match_columns.return_value = ["id"]
            res = client.post("/carica/auto-mapping", json=self._base_payload())
        assert res.status_code == 200
        data = res.json()
        assert "mappings" in data
        assert data["existing_columns"] == []

    def test_success_with_table(self, client):
        """Auto-mapping with existing table: queries existing columns."""
        mock_mapping = MagicMock(
            source_column="id", dest_column="id", dest_type="INTEGER",
            is_geometry=False, srid=4326, is_primary_key=False, skip=False,
        )
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([("id", "integer"), ("name", "varchar")]))
        with patch("app.core.carica.router._get_connection", return_value=FAKE_CONN_DICT), \
             patch("app.core.carica.router._create_engine") as mock_eng, \
             patch("app.core.carica.router.DDLGenerator") as mock_ddl, \
             patch("app.core.carica.router.ColumnMapper") as mock_cm:
            mock_conn_ctx = MagicMock()
            mock_conn_ctx.execute.return_value = mock_result
            mock_eng.return_value.connect.return_value.__enter__ = MagicMock(return_value=mock_conn_ctx)
            mock_eng.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_ddl.return_value.query_table_columns.return_value = "SELECT ..."
            mock_cm.return_value.auto_map.return_value = [mock_mapping]
            mock_cm.return_value.suggest_match_columns.return_value = []
            payload = self._base_payload(table_name="users")
            res = client.post("/carica/auto-mapping", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert len(data["existing_columns"]) == 2

    def test_table_query_fails_still_returns_mapping(self, client):
        """If querying existing columns fails, mapping still succeeds."""
        mock_mapping = MagicMock(
            source_column="id", dest_column="id", dest_type="INTEGER",
            is_geometry=False, srid=4326, is_primary_key=False, skip=False,
        )
        with patch("app.core.carica.router._get_connection", return_value=FAKE_CONN_DICT), \
             patch("app.core.carica.router._create_engine", side_effect=RuntimeError("no engine")), \
             patch("app.core.carica.router.ColumnMapper") as mock_cm:
            mock_cm.return_value.auto_map.return_value = [mock_mapping]
            mock_cm.return_value.suggest_match_columns.return_value = []
            payload = self._base_payload(table_name="missing_table")
            res = client.post("/carica/auto-mapping", json=payload)
        assert res.status_code == 200

    def test_column_type_branches(self, client):
        """Cover all type branches: int, real, bool, geometry, default."""
        mock_mapping = MagicMock(
            source_column="x", dest_column="x", dest_type="TEXT",
            is_geometry=False, srid=4326, is_primary_key=False, skip=False,
        )
        cols = [
            {"name": "a", "type": "int"},
            {"name": "b", "type": "real"},
            {"name": "c", "type": "double"},
            {"name": "d", "type": "bool"},
            {"name": "e", "type": "geometry(Point)"},
            {"name": "f", "type": "other"},
        ]
        with patch("app.core.carica.router._get_connection", return_value=FAKE_CONN_DICT), \
             patch("app.core.carica.router.ColumnMapper") as mock_cm:
            mock_cm.return_value.auto_map.return_value = [mock_mapping]
            mock_cm.return_value.suggest_match_columns.return_value = []
            payload = self._base_payload(source_columns=cols, table_name="")
            res = client.post("/carica/auto-mapping", json=payload)
        assert res.status_code == 200

    def test_exception_returns_500(self, client):
        """A crash in the endpoint returns 500."""
        with patch("app.core.carica.router._get_connection", side_effect=RuntimeError("boom")):
            # Need table_name empty so it skips the inner try and hits the
            # second _get_connection call at line 270.
            payload = self._base_payload(table_name="")
            res = client.post("/carica/auto-mapping", json=payload)
        assert res.status_code == 500
