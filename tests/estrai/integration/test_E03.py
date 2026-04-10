"""
E-03 — Oracle Spatial SDO_GEOMETRY
====================================
Verifica:
  1. Connessione Oracle 23ai (Free) funziona
  2. Tabella con colonna SDO_GEOMETRY (Point) creata correttamente
  3. Preview: geometrie convertite in WKT senza crash
  4. Colonna geometry presente con tipo geometry nel risultato
  5. Valori WKT non nulli e nel formato corretto (POINT(...))

Note architetturali:
  - Il QueryGenerator wrappa automaticamente le colonne geometry con
    SDO_UTIL.TO_WKTGEOMETRY() per Oracle
  - Oracle restituisce nomi colonne in UPPERCASE → il backend li normalizza
    in lowercase prima di restituirli
  - L'INSERT di geometrie Oracle richiede SDO_GEOMETRY constructor con
    SDO_ELEM_INFO e SDO_ORDINATES

Prerequisiti:
  - Docker Oracle 23ai Free attivo su localhost:1521
  - Backend FastAPI attivo su localhost:8000
"""

import oracledb
import pytest

pytestmark = [pytest.mark.estrai, pytest.mark.oracle]

# ─── Configurazione Oracle ────────────────────────────────────────────────────

ORACLE_CFG = {
    "host": "localhost",
    "port": 1521,
    "service": "FREEPDB1",
    "user": "dataflow",
    "password": "dataflow_test_2026",
}

TABLE_NAME = "test_e03_luoghi"

# ─── Fixture Oracle diretta (psycopg2 equivalente per Oracle) ────────────────

@pytest.fixture
def oracle_table():
    """
    Factory per creare/droppare tabelle Oracle con SDO_GEOMETRY.
    Gestisce la spatial metadata table (USER_SDO_GEOM_METADATA).
    """
    created = []

    def _create(table_name: str, ddl: str, rows: list):
        conn = oracledb.connect(
            user=ORACLE_CFG["user"],
            password=ORACLE_CFG["password"],
            dsn=f"{ORACLE_CFG['host']}:{ORACLE_CFG['port']}/{ORACLE_CFG['service']}",
        )
        cur = conn.cursor()

        # Drop se esiste
        try:
            cur.execute(f"DROP TABLE {table_name}")
            conn.commit()
        except Exception:
            pass

        # Rimuovi metadata spaziali esistenti
        try:
            cur.execute(
                "DELETE FROM USER_SDO_GEOM_METADATA WHERE TABLE_NAME = :1",
                [table_name.upper()]
            )
            conn.commit()
        except Exception:
            pass

        # Crea tabella
        cur.execute(f"CREATE TABLE {table_name} ({ddl})")

        # Inserisci metadata spaziali (obbligatorio per SDO_GEOMETRY)
        cur.execute("""
            INSERT INTO USER_SDO_GEOM_METADATA (TABLE_NAME, COLUMN_NAME, DIMINFO, SRID)
            VALUES (
                :1, 'GEOM',
                SDO_DIM_ARRAY(
                    SDO_DIM_ELEMENT('X', -180, 180, 0.005),
                    SDO_DIM_ELEMENT('Y', -90,  90,  0.005)
                ),
                4326
            )
        """, [table_name.upper()])

        for row_sql in rows:
            cur.execute(row_sql)

        conn.commit()
        cur.close()
        conn.close()

        created.append(table_name)
        return table_name

    yield _create

    # Teardown
    for tname in created:
        try:
            conn = oracledb.connect(
                user=ORACLE_CFG["user"],
                password=ORACLE_CFG["password"],
                dsn=f"{ORACLE_CFG['host']}:{ORACLE_CFG['port']}/{ORACLE_CFG['service']}",
            )
            cur = conn.cursor()
            try:
                cur.execute(f"DROP TABLE {tname}")
            except Exception:
                pass
            try:
                cur.execute(
                    "DELETE FROM USER_SDO_GEOM_METADATA WHERE TABLE_NAME = :1",
                    [tname.upper()]
                )
            except Exception:
                pass
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[WARN] Teardown Oracle tabella {tname}: {e}")


# ─── Dati di test ─────────────────────────────────────────────────────────────

TABLE_DDL = """
    id    NUMBER PRIMARY KEY,
    nome  VARCHAR2(100),
    geom  SDO_GEOMETRY
"""

TABLE_ROWS = [
    f"""INSERT INTO {TABLE_NAME} (id, nome, geom) VALUES (
        1, 'Roma',
        SDO_GEOMETRY(2001, 4326,
            SDO_POINT_TYPE(12.4964, 41.9028, NULL), NULL, NULL)
    )""",
    f"""INSERT INTO {TABLE_NAME} (id, nome, geom) VALUES (
        2, 'Milano',
        SDO_GEOMETRY(2001, 4326,
            SDO_POINT_TYPE(9.1900, 45.4642, NULL), NULL, NULL)
    )""",
    f"""INSERT INTO {TABLE_NAME} (id, nome, geom) VALUES (
        3, 'Napoli',
        SDO_GEOMETRY(2001, 4326,
            SDO_POINT_TYPE(14.2681, 40.8518, NULL), NULL, NULL)
    )""",
]

CANVAS_COLUMNS = [
    {"name": "id",   "type": "int",      "visible": True},
    {"name": "nome", "type": "str",      "visible": True},
    {"name": "geom", "type": "geometry", "visible": True},
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
                "fileName": "e03_output",
                "format": "csv",
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "node-source", "target": "node-result"}
        ],
    }


# ─── Test ─────────────────────────────────────────────────────────────────────

class TestE03:

    def test_preview_sdo_no_crash(self, client, oracle_conn, oracle_table, make_project, poll):
        """
        E-03a: preview con colonna SDO_GEOMETRY non deve crashare.
        Il backend converte automaticamente tramite SDO_UTIL.TO_WKTGEOMETRY().
        """
        oracle_table(table_name=TABLE_NAME, ddl=TABLE_DDL, rows=TABLE_ROWS)

        canvas = _build_canvas(TABLE_NAME)
        project_id = make_project(
            name="[AUTO] E-03 Oracle SDO preview",
            project_type="estrai",
            connection_id=oracle_conn,
            canvas=canvas,
        )

        res = client.get(f"/projects/{project_id}/preview", params={"limit": 10})
        assert res.status_code == 200, f"Avvio preview fallito: {res.text}"
        task_id = res.json().get("task_id")
        assert task_id

        result = poll(task_id)
        assert result.get("success") is True, f"Preview fallita (crash): {result}"

    def test_preview_righe_e_geometrie_wkt(self, client, oracle_conn, oracle_table, make_project, poll):
        """
        E-03b: le righe sono 3, la colonna geom contiene valori WKT POINT(...).
        """
        oracle_table(table_name=TABLE_NAME, ddl=TABLE_DDL, rows=TABLE_ROWS)

        canvas = _build_canvas(TABLE_NAME)
        project_id = make_project(
            name="[AUTO] E-03 Oracle WKT values",
            project_type="estrai",
            connection_id=oracle_conn,
            canvas=canvas,
        )

        res = client.get(f"/projects/{project_id}/preview", params={"limit": 10})
        assert res.status_code == 200
        task_id = res.json().get("task_id")
        result = poll(task_id)
        assert result.get("success") is True

        rows = result.get("rows", [])
        assert len(rows) == 3, f"Attese 3 righe, trovate {len(rows)}"

        # Ogni riga deve avere geom non nulla con formato WKT POINT
        for row in rows:
            geom_val = row.get("geom")
            assert geom_val is not None, f"geom NULL per riga: {row}"
            assert isinstance(geom_val, str), f"geom non è stringa: {type(geom_val)}"
            assert geom_val.upper().startswith("POINT"), (
                f"WKT atteso 'POINT(...)', trovato: '{geom_val}'"
            )

    def test_tipo_colonna_geometry(self, client, oracle_conn, oracle_table, make_project, poll):
        """
        E-03c: la colonna geom ha tipo 'geometry' nel risultato.
        """
        oracle_table(table_name=TABLE_NAME, ddl=TABLE_DDL, rows=TABLE_ROWS)

        canvas = _build_canvas(TABLE_NAME)
        project_id = make_project(
            name="[AUTO] E-03 Oracle tipo geometry",
            project_type="estrai",
            connection_id=oracle_conn,
            canvas=canvas,
        )

        res = client.get(f"/projects/{project_id}/preview", params={"limit": 10})
        assert res.status_code == 200
        task_id = res.json().get("task_id")
        result = poll(task_id)
        assert result.get("success") is True

        columns = result.get("columns", [])
        col_map = {c["name"]: c["type"] for c in columns}

        assert "geom" in col_map, f"Colonna 'geom' mancante. Trovate: {list(col_map.keys())}"
        assert col_map["geom"].startswith("geometry"), (
            f"Tipo 'geom' atteso 'geometry*', trovato '{col_map['geom']}'"
        )
