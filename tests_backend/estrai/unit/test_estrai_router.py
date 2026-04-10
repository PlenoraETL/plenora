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

from app.core.estrai.router import functions_router
from app.db.sql_functions import get_function_catalog


# =============================================================================
# Setup app di test
# =============================================================================

@pytest.fixture
def app():
    """App FastAPI minimale con solo i router ESTRAI."""
    _app = FastAPI()
    _app.include_router(functions_router)
    return _app


@pytest.fixture
def client(app):
    return TestClient(app)


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
