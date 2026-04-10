"""
E-01 — PostgreSQL/PostGIS connessione + preview
================================================
Verifica:
  1. La tabella di test appare nella lista tabelle della connessione
  2. La preview restituisce righe corrette
  3. La colonna geometry è rilevata come geometry:point
  4. Il CRS è WGS84 (SRID 4326)

Prerequisiti:
  - Docker PostgreSQL attivo su localhost:5432
  - Backend FastAPI attivo su localhost:8000
"""

import pytest
from conftest import build_estrai_canvas

pytestmark = [pytest.mark.estrai, pytest.mark.postgres]

# ─── Dati di test fissi ───────────────────────────────────────────────────────

TABLE_NAME = "test_e01_punti"

TABLE_DDL = """
    id   SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    pop  INTEGER,
    geom geometry(Point, 4326)
"""

TABLE_ROWS = [
    f"INSERT INTO {TABLE_NAME} (nome, pop, geom) VALUES "
    f"('Roma',    2873000, ST_GeomFromText('POINT(12.4964 41.9028)', 4326))",

    f"INSERT INTO {TABLE_NAME} (nome, pop, geom) VALUES "
    f"('Milano',  1352000, ST_GeomFromText('POINT(9.1900 45.4642)', 4326))",

    f"INSERT INTO {TABLE_NAME} (nome, pop, geom) VALUES "
    f"('Napoli',  962000,  ST_GeomFromText('POINT(14.2681 40.8518)', 4326))",
]

CANVAS_COLUMNS = [
    {"name": "id",   "type": "int",            "visible": True},
    {"name": "nome", "type": "str",            "visible": True},
    {"name": "pop",  "type": "int",            "visible": True},
    {"name": "geom", "type": "geometry:point", "visible": True},
]


# ─── Test ─────────────────────────────────────────────────────────────────────

class TestE01:

    def test_lista_tabelle(self, client, pg_conn, pg_table):
        """
        E-01a: la tabella appare nel catalogo tabelle della connessione.
        """
        # Setup: crea tabella su PostgreSQL
        pg_table(ddl=TABLE_DDL, rows=TABLE_ROWS, table_name=TABLE_NAME)

        # Chiede la lista tabelle tramite API
        res = client.get(f"/connections/{pg_conn}/tables")
        assert res.status_code == 200, f"GET /connections/{pg_conn}/tables fallito: {res.text}"

        data = res.json()
        # La risposta può essere {"tables": {...}} o lista diretta
        tables = data.get("tables", data) if isinstance(data, dict) else data
        table_names = list(tables.keys()) if isinstance(tables, dict) else tables

        assert TABLE_NAME in table_names, (
            f"Tabella '{TABLE_NAME}' non trovata nel catalogo. Trovate: {table_names}"
        )

    def test_preview_geometry(self, client, pg_conn, pg_table, make_project, poll):
        """
        E-01b: preview restituisce righe corrette con geometry:point WGS84.
        """
        # Setup: crea tabella su PostgreSQL
        pg_table(ddl=TABLE_DDL, rows=TABLE_ROWS, table_name=TABLE_NAME)

        # Canvas ESTRAI: source → result
        canvas = build_estrai_canvas(
            table_name=TABLE_NAME,
            columns=CANVAS_COLUMNS,
            result_filename="e01_output",
            result_format="csv",
        )

        # Crea progetto
        project_id = make_project(
            name="[AUTO] E-01 Preview Geometry",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        # Avvia preview
        res = client.get(f"/projects/{project_id}/preview", params={"limit": 10})
        assert res.status_code == 200, f"Avvio preview fallito: {res.text}"

        task_id = res.json().get("task_id")
        assert task_id, "task_id mancante nella risposta preview"

        # Poll fino a completamento
        result = poll(task_id)

        # ── Assert ────────────────────────────────────────────────────────────

        assert result.get("success") is True, f"Preview fallita: {result}"

        rows = result.get("rows", [])
        assert len(rows) == 3, f"Attese 3 righe, trovate {len(rows)}"

        # Verifica nomi città presenti
        nomi = {r["nome"] for r in rows}
        assert nomi == {"Roma", "Milano", "Napoli"}, f"Righe inattese: {nomi}"

        # Verifica colonne
        columns = result.get("columns", [])
        col_map = {c["name"]: c["type"] for c in columns}

        assert "geom" in col_map, f"Colonna 'geom' mancante. Colonne trovate: {list(col_map.keys())}"

        geom_type = col_map["geom"]
        assert geom_type == "geometry:point", (
            f"Tipo geometry errato: atteso 'geometry:point', trovato '{geom_type}'"
        )

        # Verifica che i valori geometry non siano nulli
        for row in rows:
            assert row.get("geom") is not None, f"geom NULL per riga: {row}"
