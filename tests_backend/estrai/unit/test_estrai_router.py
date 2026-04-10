"""
tests/estrai/unit/test_estrai_router.py
========================================
Unit test sul router ESTRAI (funzioni pure e logica di validazione).

NON richiede backend avviato. Testa:
  - generate_sql endpoint: validazione input (progetto mancante, canvas vuoto,
    nodo result mancante) con mock dei servizi
  - functions router: get_catalog, translate, get_supported_functions
  - Mutation score: status code e messaggi di errore esatti

NON testa il comportamento HTTP completo (quello è nei test integration).
Testa la logica di validazione e i percorsi di errore con FastAPI TestClient.
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

from app.core.estrai.router import functions_router, sql_router, init_estrai
from app.db.sql_functions import get_function_catalog


# =============================================================================
# Setup app di test
# =============================================================================

@pytest.fixture
def app():
    """App FastAPI minimale con solo i router ESTRAI."""
    _app = FastAPI()
    _app.include_router(functions_router)
    _app.include_router(sql_router)
    return _app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def mock_services():
    """Mock di tutti i servizi usati dal router."""
    project_service = MagicMock()
    connection_service = MagicMock()
    query_generator = MagicMock()
    init_estrai(project_service, connection_service, query_generator)
    return project_service, connection_service, query_generator


# =============================================================================
# GET /functions/catalog
# =============================================================================

class TestGetCatalog:

    def test_status_200(self, client):
        res = client.get("/functions/catalog")
        assert res.status_code == 200

    def test_ha_chiave_functions(self, client):
        res = client.get("/functions/catalog")
        data = res.json()
        assert "functions" in data

    def test_ha_chiave_by_category(self, client):
        res = client.get("/functions/catalog")
        data = res.json()
        assert "by_category" in data

    def test_functions_non_vuoto(self, client):
        res = client.get("/functions/catalog")
        data = res.json()
        assert len(data["functions"]) > 0

    def test_categorie_presenti(self, client):
        res = client.get("/functions/catalog")
        data = res.json()
        cats = data.get("categories", {})
        assert "geometric" in cats or "numeric" in cats


# =============================================================================
# GET /functions/supported/{db_type}
# =============================================================================

class TestGetSupportedFunctions:

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_db_valido_status_200(self, client, db_type):
        res = client.get(f"/functions/supported/{db_type}")
        assert res.status_code == 200

    def test_db_invalido_status_400(self, client):
        """Mutation target: DB non valido → 400, non 200."""
        res = client.get("/functions/supported/db2_inesistente")
        assert res.status_code == 400

    def test_risposta_ha_supported(self, client):
        res = client.get("/functions/supported/postgres")
        data = res.json()
        assert "supported" in data

    def test_risposta_ha_db_type(self, client):
        res = client.get("/functions/supported/postgres")
        data = res.json()
        assert data.get("db_type") == "postgres"

    def test_risposta_ha_unsupported(self, client):
        res = client.get("/functions/supported/postgres")
        data = res.json()
        assert "unsupported" in data


# =============================================================================
# POST /functions/translate
# =============================================================================

class TestTranslateEndpoint:

    def test_translate_valido(self, client):
        res = client.post("/functions/translate", json={
            "db_type": "postgres",
            "function_name": "UPPER",
            "params": {"column": "nome"},
            "table_alias": "t",
        })
        assert res.status_code == 200
        data = res.json()
        assert "sql" in data
        assert "nome" in data["sql"]

    def test_translate_con_expression(self, client):
        res = client.post("/functions/translate", json={
            "db_type": "postgres",
            "expression": {"function": "UPPER", "params": {"column": "col"}},
            "column_name": "col_up",
        })
        assert res.status_code == 200

    def test_translate_senza_expression_e_function_name_400(self, client):
        """Mutation target: né expression né function_name → 400."""
        res = client.post("/functions/translate", json={
            "db_type": "postgres",
        })
        assert res.status_code == 400

    def test_translate_funzione_inesistente_400(self, client):
        res = client.post("/functions/translate", json={
            "db_type": "postgres",
            "function_name": "FUNZIONE_XYZ_INESISTENTE",
            "params": {},
        })
        assert res.status_code == 400

    def test_translate_db_invalido_400(self, client):
        res = client.post("/functions/translate", json={
            "db_type": "db_inesistente",
            "function_name": "UPPER",
            "params": {"column": "col"},
        })
        assert res.status_code == 400


# =============================================================================
# POST /projects/{id}/generate-sql
# =============================================================================

class TestGenerateSql:

    def test_progetto_non_trovato_404(self, client, mock_services):
        project_service, _, _ = mock_services
        project_service.get_project_metadata.return_value = None

        res = client.post("/projects/progetto_inesistente/generate-sql")
        assert res.status_code == 404

    def test_canvas_non_trovato_400(self, client, mock_services):
        project_service, _, _ = mock_services
        project_service.get_project_metadata.return_value = MagicMock(
            default_connection_id="conn-1"
        )
        project_service.get_project_canvas.return_value = None

        res = client.post("/projects/proj-1/generate-sql")
        assert res.status_code == 400

    def test_nodo_result_mancante_400(self, client, mock_services):
        project_service, _, _ = mock_services
        project_service.get_project_metadata.return_value = MagicMock(
            default_connection_id="conn-1"
        )
        canvas = MagicMock()
        canvas.nodes = []  # nessun nodo result
        project_service.get_project_canvas.return_value = canvas

        res = client.post("/projects/proj-1/generate-sql")
        assert res.status_code == 400

    def test_generate_sql_success(self, client, mock_services):
        project_service, connection_service, query_generator = mock_services

        # Mock metadata
        project_service.get_project_metadata.return_value = MagicMock(
            default_connection_id="conn-1"
        )

        # Mock canvas con nodo result
        result_node = MagicMock()
        result_node.type = "result"
        result_node.id = "result-1"
        canvas = MagicMock()
        canvas.nodes = [result_node]
        canvas.edges = []
        project_service.get_project_canvas.return_value = canvas

        # Mock connessione
        conn = MagicMock()
        conn.type = "postgres"
        conn.db_schema = None
        connection_service.get_connection.return_value = conn

        # Mock generatore SQL
        query_generator.generate_sql.return_value = {
            "sql": "SELECT id FROM tab",
            "params": {},
        }

        res = client.post("/projects/proj-1/generate-sql")
        assert res.status_code == 200
        data = res.json()
        assert "sql" in data
        assert "SELECT" in data["sql"]


# =============================================================================
# get_function_catalog — funzione pura (non router)
# =============================================================================

class TestGetFunctionCatalogPure:

    def test_catalogo_non_vuoto(self):
        catalog = get_function_catalog()
        assert len(catalog) > 0

    def test_ogni_funzione_ha_category(self):
        catalog = get_function_catalog()
        for name, func in catalog.items():
            assert "category" in func, f"{name}: manca category"

    def test_category_values_validi(self):
        """Mutation target: category deve essere uno dei valori validi."""
        catalog = get_function_catalog()
        valid = {"geometric", "numeric", "string", "date", "conditional", "cast"}
        for name, func in catalog.items():
            assert func["category"] in valid, \
                f"{name}: category '{func['category']}' non valida"
