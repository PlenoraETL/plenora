"""
E-04 — SQL Server connessione + preview
========================================
Verifica:
  1. Connessione SQL Server 2022 (Developer) funziona
  2. La tabella di test appare nella lista tabelle della connessione
  3. Preview restituisce righe corrette
  4. Tipi colonne mappati correttamente dal driver MSSQL:
       INT        → int
       NVARCHAR   → str
       DECIMAL    → real
       BIT        → bool
       DATETIME2  → date

Prerequisiti:
  - Docker SQL Server 2022 attivo su localhost:1433
  - Backend FastAPI attivo su localhost:8000
"""

import pyodbc
import pytest

pytestmark = [pytest.mark.estrai, pytest.mark.mssql]

# ─── Configurazione SQL Server ────────────────────────────────────────────────

MSSQL_CFG = {
    "host":     "localhost",
    "port":     1433,
    "database": "master",
    "user":     "sa",
    "password": "DataFlow_Test_2026!",
}

TABLE_NAME = "test_e04_prodotti"


def _mssql_conn():
    """Crea connessione pyodbc diretta a SQL Server."""
    # Prova Driver 18, fallback a Driver 17
    for driver in ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"]:
        try:
            return pyodbc.connect(
                f"DRIVER={{{driver}}};"
                f"SERVER={MSSQL_CFG['host']},{MSSQL_CFG['port']};"
                f"DATABASE={MSSQL_CFG['database']};"
                f"UID={MSSQL_CFG['user']};"
                f"PWD={MSSQL_CFG['password']};"
                "TrustServerCertificate=yes;",
                autocommit=True,
            )
        except pyodbc.Error:
            continue
    raise RuntimeError("Nessun ODBC Driver SQL Server trovato (18 o 17)")


# ─── Fixture tabella MSSQL ────────────────────────────────────────────────────

@pytest.fixture
def mssql_table():
    """Factory: crea tabella MSSQL + dati, droppa al teardown."""
    created = []

    def _create(table_name: str, ddl: str, rows: list):
        conn = _mssql_conn()
        cur = conn.cursor()

        cur.execute(f"IF OBJECT_ID('{table_name}', 'U') IS NOT NULL DROP TABLE {table_name}")
        cur.execute(f"CREATE TABLE {table_name} ({ddl})")
        for row_sql in rows:
            cur.execute(row_sql)

        cur.close()
        conn.close()
        created.append(table_name)
        return table_name

    yield _create

    for tname in created:
        try:
            conn = _mssql_conn()
            cur = conn.cursor()
            cur.execute(f"IF OBJECT_ID('{tname}', 'U') IS NOT NULL DROP TABLE {tname}")
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[WARN] Teardown MSSQL tabella {tname}: {e}")


# ─── Dati di test ─────────────────────────────────────────────────────────────

TABLE_DDL = """
    id          INT PRIMARY KEY,
    nome        NVARCHAR(100) NOT NULL,
    prezzo      DECIMAL(10,2),
    disponibile BIT,
    creato_il   DATETIME2
"""

TABLE_ROWS = [
    f"INSERT INTO {TABLE_NAME} VALUES (1, 'Prodotto A', 29.99, 1, '2025-01-15 10:00:00')",
    f"INSERT INTO {TABLE_NAME} VALUES (2, 'Prodotto B', 149.50, 0, '2025-03-01 08:30:00')",
    f"INSERT INTO {TABLE_NAME} VALUES (3, 'Prodotto C', 9.90,  1, '2025-06-10 14:00:00')",
]

CANVAS_COLUMNS = [
    {"name": "id",          "type": "int",  "visible": True},
    {"name": "nome",        "type": "str",  "visible": True},
    {"name": "prezzo",      "type": "real", "visible": True},
    {"name": "disponibile", "type": "bool", "visible": True},
    {"name": "creato_il",   "type": "date", "visible": True},
]


def _build_canvas(table_name: str) -> dict:
    return {
        "nodes": [
            {
                "id": "node-source",
                "type": "source",
                "position": {"x": 100, "y": 200},
                "table_name": table_name,
                "columns": CANVAS_COLUMNS,
                "filters": [],
            },
            {
                "id": "node-result",
                "type": "result",
                "position": {"x": 500, "y": 200},
                "fileName": "e04_output",
                "format": "csv",
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "node-source", "target": "node-result"}
        ],
    }


# ─── Test ─────────────────────────────────────────────────────────────────────

class TestE04:

    def test_lista_tabelle(self, client, mssql_conn, mssql_table):
        """
        E-04a: la tabella appare nel catalogo tabelle della connessione MSSQL.
        """
        mssql_table(table_name=TABLE_NAME, ddl=TABLE_DDL, rows=TABLE_ROWS)

        res = client.get(f"/connections/{mssql_conn}/tables")
        assert res.status_code == 200, f"GET tables fallito: {res.text}"

        data = res.json()
        tables = data.get("tables", data) if isinstance(data, dict) else data
        table_names = [t.lower() for t in (tables.keys() if isinstance(tables, dict) else tables)]

        assert TABLE_NAME.lower() in table_names, (
            f"Tabella '{TABLE_NAME}' non trovata. Trovate: {table_names[:10]}"
        )

    def test_preview_righe(self, client, mssql_conn, mssql_table, make_project, poll):
        """
        E-04b: preview restituisce le 3 righe corrette.
        """
        mssql_table(table_name=TABLE_NAME, ddl=TABLE_DDL, rows=TABLE_ROWS)

        canvas = _build_canvas(TABLE_NAME)
        project_id = make_project(
            name="[AUTO] E-04 MSSQL preview",
            project_type="estrai",
            connection_id=mssql_conn,
            canvas=canvas,
        )

        res = client.get(f"/projects/{project_id}/preview", params={"limit": 10})
        assert res.status_code == 200, f"Avvio preview fallito: {res.text}"
        task_id = res.json().get("task_id")
        assert task_id

        result = poll(task_id)
        assert result.get("success") is True, f"Preview fallita: {result}"

        rows = result.get("rows", [])
        assert len(rows) == 3, f"Attese 3 righe, trovate {len(rows)}"

        nomi = {r.get("nome") for r in rows}
        assert nomi == {"Prodotto A", "Prodotto B", "Prodotto C"}, f"Nomi inattesi: {nomi}"

    def test_tipi_mappati(self, client, mssql_conn, mssql_table, make_project, poll):
        """
        E-04c: tipi colonne mappati correttamente dal driver MSSQL.
          INT       → int
          NVARCHAR  → str
          DECIMAL   → real
          BIT       → bool
          DATETIME2 → date
        """
        mssql_table(table_name=TABLE_NAME, ddl=TABLE_DDL, rows=TABLE_ROWS)

        canvas = _build_canvas(TABLE_NAME)
        project_id = make_project(
            name="[AUTO] E-04 MSSQL tipi",
            project_type="estrai",
            connection_id=mssql_conn,
            canvas=canvas,
        )

        res = client.get(f"/projects/{project_id}/preview", params={"limit": 10})
        assert res.status_code == 200
        task_id = res.json().get("task_id")
        result = poll(task_id)
        assert result.get("success") is True

        columns = result.get("columns", [])
        col_map = {c["name"]: c["type"] for c in columns}

        assert col_map.get("id")          == "int",  f"id: atteso int, trovato {col_map.get('id')}"
        assert col_map.get("nome")        == "str",  f"nome: atteso str, trovato {col_map.get('nome')}"
        assert col_map.get("prezzo")      == "real", f"prezzo: atteso real, trovato {col_map.get('prezzo')}"
        assert col_map.get("disponibile") == "bool", f"disponibile: atteso bool, trovato {col_map.get('disponibile')}"
        assert col_map.get("creato_il")   == "date", f"creato_il: atteso date, trovato {col_map.get('creato_il')}"
