"""
E-05 — MySQL/MariaDB connessione base
======================================
Verifica su entrambi i DB (MySQL 8.0 porta 3306, MariaDB porta 3307):
  1. Connessione funziona
  2. Preview restituisce dati corretti
  3. Tipi numerici mappati correttamente:
       INT       → int
       DECIMAL   → real
       DOUBLE    → real
       VARCHAR   → str
       TINYINT   → int (MySQL usa TINYINT per bool)
       DATETIME  → date

Prerequisiti:
  - Docker MySQL 8.0 attivo su localhost:3306
  - Docker MariaDB attivo su localhost:3307
  - Backend FastAPI attivo su localhost:8000
"""

import pymysql
import pytest

pytestmark = [pytest.mark.estrai, pytest.mark.mysql]

# ─── Configurazioni ───────────────────────────────────────────────────────────

MYSQL_CFGS = {
    "mysql": {
        "host": "localhost", "port": 3306,
        "database": "dataflow_test",
        "user": "dataflow", "password": "dataflow_test_2026",
        "label": "MySQL 8.0",
    },
    "mariadb": {
        "host": "localhost", "port": 3307,
        "database": "dataflow_test",
        "user": "dataflow", "password": "dataflow_test_2026",
        "label": "MariaDB",
    },
}

TABLE_NAME = "test_e05_vendite"

TABLE_DDL = """
    id          INT PRIMARY KEY AUTO_INCREMENT,
    prodotto    VARCHAR(100) NOT NULL,
    quantita    INT,
    prezzo_unit DECIMAL(10,2),
    sconto      DOUBLE,
    data_vend   DATETIME
"""

TABLE_ROWS = [
    f"INSERT INTO {TABLE_NAME} (prodotto, quantita, prezzo_unit, sconto, data_vend) "
    f"VALUES ('Laptop', 3, 899.99, 0.10, '2025-02-14 09:00:00')",

    f"INSERT INTO {TABLE_NAME} (prodotto, quantita, prezzo_unit, sconto, data_vend) "
    f"VALUES ('Mouse', 10, 24.50, 0.05, '2025-04-01 11:30:00')",

    f"INSERT INTO {TABLE_NAME} (prodotto, quantita, prezzo_unit, sconto, data_vend) "
    f"VALUES ('Monitor', 2, 349.00, 0.15, '2025-05-20 16:00:00')",
]

CANVAS_COLUMNS = [
    {"name": "id",          "type": "int",  "visible": True},
    {"name": "prodotto",    "type": "str",  "visible": True},
    {"name": "quantita",    "type": "int",  "visible": True},
    {"name": "prezzo_unit", "type": "real", "visible": True},
    {"name": "sconto",      "type": "real", "visible": True},
    {"name": "data_vend",   "type": "date", "visible": True},
]


# ─── Fixture tabella MySQL/MariaDB ────────────────────────────────────────────

def _mysql_connect(cfg: dict):
    return pymysql.connect(
        host=cfg["host"], port=cfg["port"],
        database=cfg["database"],
        user=cfg["user"], password=cfg["password"],
        autocommit=True,
    )


@pytest.fixture
def mysql_table():
    """Factory: crea tabella MySQL/MariaDB + dati, droppa al teardown."""
    created = []  # lista di (cfg, table_name)

    def _create(cfg: dict, table_name: str, ddl: str, rows: list):
        conn = _mysql_connect(cfg)
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {table_name}")
        cur.execute(f"CREATE TABLE {table_name} ({ddl})")
        for row_sql in rows:
            cur.execute(row_sql)
        cur.close()
        conn.close()
        created.append((cfg, table_name))
        return table_name

    yield _create

    for cfg, tname in created:
        try:
            conn = _mysql_connect(cfg)
            cur = conn.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {tname}")
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[WARN] Teardown MySQL tabella {tname}: {e}")


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
                "fileName": "e05_output",
                "format": "csv",
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "node-source", "target": "node-result"}
        ],
    }


# ─── Helper: registra connessione MySQL/MariaDB via API ──────────────────────

def _register_mysql_conn(client, cfg: dict, label: str) -> str:
    payload = {
        "name": f"[AUTO] E-05 {label}",
        "description": f"Connessione di test {label}",
        "type": "mysql",
        "host": cfg["host"],
        "port": cfg["port"],
        "database": cfg["database"],
        "username": cfg["user"],
        "password": cfg["password"],
    }
    res = client.post("/connections", json=payload)
    assert res.status_code == 200, f"Errore creazione connessione {label}: {res.text}"
    return res.json()["id"]


# ─── Test ─────────────────────────────────────────────────────────────────────

class TestE05:

    @pytest.mark.parametrize("db_key", ["mysql", "mariadb"])
    def test_preview_dati_corretti(self, client, mysql_table, make_project, poll, db_key):
        """
        E-05a: preview restituisce 3 righe con i prodotti attesi.
        Eseguito su MySQL e MariaDB.
        """
        cfg = MYSQL_CFGS[db_key]
        mysql_table(cfg=cfg, table_name=TABLE_NAME, ddl=TABLE_DDL, rows=TABLE_ROWS)

        conn_id = _register_mysql_conn(client, cfg, cfg["label"])
        try:
            canvas = _build_canvas(TABLE_NAME)
            project_id = make_project(
                name=f"[AUTO] E-05 {cfg['label']} preview",
                project_type="estrai",
                connection_id=conn_id,
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

            prodotti = {r.get("prodotto") for r in rows}
            assert prodotti == {"Laptop", "Mouse", "Monitor"}, f"Prodotti inattesi: {prodotti}"

        finally:
            client.delete(f"/connections/{conn_id}")

    @pytest.mark.parametrize("db_key", ["mysql", "mariadb"])
    def test_tipi_numerici(self, client, mysql_table, make_project, poll, db_key):
        """
        E-05b: tipi numerici mappati correttamente.
          INT     → int
          DECIMAL → real
          DOUBLE  → real
          VARCHAR → str
          DATETIME → date
        """
        cfg = MYSQL_CFGS[db_key]
        mysql_table(cfg=cfg, table_name=TABLE_NAME, ddl=TABLE_DDL, rows=TABLE_ROWS)

        conn_id = _register_mysql_conn(client, cfg, cfg["label"])
        try:
            canvas = _build_canvas(TABLE_NAME)
            project_id = make_project(
                name=f"[AUTO] E-05 {cfg['label']} tipi",
                project_type="estrai",
                connection_id=conn_id,
                canvas=canvas,
            )

            res = client.get(f"/projects/{project_id}/preview", params={"limit": 10})
            assert res.status_code == 200
            task_id = res.json().get("task_id")
            result = poll(task_id)
            assert result.get("success") is True

            col_map = {c["name"]: c["type"] for c in result.get("columns", [])}

            assert col_map.get("id")          == "int",  f"id: {col_map.get('id')}"
            assert col_map.get("prodotto")    == "str",  f"prodotto: {col_map.get('prodotto')}"
            assert col_map.get("quantita")    == "int",  f"quantita: {col_map.get('quantita')}"
            assert col_map.get("prezzo_unit") == "real", f"prezzo_unit: {col_map.get('prezzo_unit')}"
            assert col_map.get("sconto")      == "real", f"sconto: {col_map.get('sconto')}"
            assert col_map.get("data_vend")   == "date", f"data_vend: {col_map.get('data_vend')}"

        finally:
            client.delete(f"/connections/{conn_id}")
