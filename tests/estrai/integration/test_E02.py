"""
E-02 — PostgreSQL query custom JOIN + WHERE su campo data
==========================================================
Verifica:
  1. JOIN tra due tabelle su chiave esterna
  2. Filtro WHERE su campo data (>= data fissa)
  3. Risultato contiene solo le righe attese
  4. Tipi colonne inferiti correttamente (int, str, date, real)

Prerequisiti:
  - Docker PostgreSQL attivo su localhost:5432
  - Backend FastAPI attivo su localhost:8000
"""

import pytest

pytestmark = [pytest.mark.estrai, pytest.mark.postgres]

# ─── Tabelle di test ──────────────────────────────────────────────────────────

TABLE_CLIENTI   = "test_e02_clienti"
TABLE_ORDINI    = "test_e02_ordini"

DDL_CLIENTI = """
    id      SERIAL PRIMARY KEY,
    nome    TEXT NOT NULL,
    citta   TEXT
"""

DDL_ORDINI = """
    id          SERIAL PRIMARY KEY,
    cliente_id  INTEGER NOT NULL,
    data_ordine DATE NOT NULL,
    importo     NUMERIC(10,2)
"""

ROWS_CLIENTI = [
    f"INSERT INTO {TABLE_CLIENTI} (nome, citta) VALUES ('Rossi Mario', 'Roma')",
    f"INSERT INTO {TABLE_CLIENTI} (nome, citta) VALUES ('Bianchi Anna', 'Milano')",
    f"INSERT INTO {TABLE_CLIENTI} (nome, citta) VALUES ('Verdi Luigi', 'Napoli')",
]

ROWS_ORDINI = [
    # cliente 1 — 2 ordini: uno vecchio, uno recente
    f"INSERT INTO {TABLE_ORDINI} (cliente_id, data_ordine, importo) VALUES (1, '2024-01-15', 150.00)",
    f"INSERT INTO {TABLE_ORDINI} (cliente_id, data_ordine, importo) VALUES (1, '2025-03-10', 320.50)",
    # cliente 2 — 1 ordine recente
    f"INSERT INTO {TABLE_ORDINI} (cliente_id, data_ordine, importo) VALUES (2, '2025-06-01', 89.90)",
    # cliente 3 — 1 ordine vecchio (deve essere escluso dal filtro)
    f"INSERT INTO {TABLE_ORDINI} (cliente_id, data_ordine, importo) VALUES (3, '2023-12-01', 500.00)",
]

# Filtro: solo ordini dal 2025-01-01 in poi → 2 righe attese (cliente 1 e 2)
FILTRO_DATA = "2025-01-01"
RIGHE_ATTESE = 2


def _build_join_canvas(table_clienti: str, table_ordini: str) -> dict:
    """
    Canvas ESTRAI con:
      - nodo source "clienti" (id, nome, citta)
      - nodo source "ordini"  (id, cliente_id, data_ordine, importo)
      - edge JOIN inner su clienti.id = ordini.cliente_id
      - filtro WHERE data_ordine >= '2025-01-01' sul nodo ordini
      - nodo result
    """
    node_clienti_id = "node-clienti"
    node_ordini_id  = "node-ordini"
    node_result_id  = "node-result"

    return {
        "nodes": [
            {
                "id": node_clienti_id,
                "type": "source",
                "position": {"x": 50, "y": 150},
                "table_name": table_clienti,
                "columns": [
                    {"name": "id",    "type": "int", "visible": True},
                    {"name": "nome",  "type": "str", "visible": True},
                    {"name": "citta", "type": "str", "visible": True},
                ],
                "filters": [],
            },
            {
                "id": node_ordini_id,
                "type": "source",
                "position": {"x": 50, "y": 400},
                "table_name": table_ordini,
                "columns": [
                    {"name": "id",          "type": "int",  "visible": False},  # nascosta
                    {"name": "cliente_id",  "type": "int",  "visible": False},  # nascosta
                    {"name": "data_ordine", "type": "date", "visible": True},
                    {"name": "importo",     "type": "real", "visible": True},
                ],
                "filters": [
                    {
                        "column": "data_ordine",
                        "operator": ">=",
                        "value": FILTRO_DATA,
                    }
                ],
            },
            {
                "id": node_result_id,
                "type": "result",
                "position": {"x": 550, "y": 275},
                "fileName": "e02_output",
                "format": "csv",
            },
        ],
        "edges": [
            # JOIN: clienti → ordini (inner join su id = cliente_id)
            {
                "id": "edge-join",
                "source": node_clienti_id,
                "target": node_ordini_id,
                "join_type": "inner",
                "source_column": "id",
                "target_column": "cliente_id",
            },
            # ordini → result
            {
                "id": "edge-result",
                "source": node_ordini_id,
                "target": node_result_id,
            },
        ],
    }


# ─── Test ─────────────────────────────────────────────────────────────────────

class TestE02:

    def test_join_where_data(self, client, pg_conn, pg_table, make_project, poll):
        """
        E-02: JOIN tra clienti e ordini con filtro WHERE data_ordine >= 2025-01-01.
        Attese 2 righe (Rossi Mario + Bianchi Anna), escluso Verdi Luigi (ordine 2023).
        """
        # Setup: crea entrambe le tabelle
        pg_table(ddl=DDL_CLIENTI, rows=ROWS_CLIENTI, table_name=TABLE_CLIENTI)
        pg_table(ddl=DDL_ORDINI,  rows=ROWS_ORDINI,  table_name=TABLE_ORDINI)

        canvas = _build_join_canvas(TABLE_CLIENTI, TABLE_ORDINI)

        project_id = make_project(
            name="[AUTO] E-02 JOIN+WHERE data",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        # Esegui
        res = client.post(f"/projects/{project_id}/execute")
        assert res.status_code == 200, f"Avvio execute fallito: {res.text}"
        task_id = res.json().get("task_id")
        assert task_id, "task_id mancante"

        result = poll(task_id)
        assert result.get("success") is True, f"Execute fallito: {result}"

    def test_righe_corrette(self, client, pg_conn, pg_table, make_project, poll):
        """
        E-02b: verifica che le righe nel risultato siano esattamente quelle attese.
        """
        pg_table(ddl=DDL_CLIENTI, rows=ROWS_CLIENTI, table_name=TABLE_CLIENTI)
        pg_table(ddl=DDL_ORDINI,  rows=ROWS_ORDINI,  table_name=TABLE_ORDINI)

        canvas = _build_join_canvas(TABLE_CLIENTI, TABLE_ORDINI)

        project_id = make_project(
            name="[AUTO] E-02 Righe corrette",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        # Usa preview per leggere le righe direttamente
        res = client.get(f"/projects/{project_id}/preview", params={"limit": 50})
        assert res.status_code == 200, f"Avvio preview fallito: {res.text}"
        task_id = res.json().get("task_id")
        assert task_id, "task_id mancante"

        result = poll(task_id)
        assert result.get("success") is True, f"Preview fallita: {result}"

        rows = result.get("rows", [])
        assert len(rows) == RIGHE_ATTESE, (
            f"Attese {RIGHE_ATTESE} righe, trovate {len(rows)}: {rows}"
        )

        # Nomi attesi (solo ordini >= 2025-01-01)
        nomi = {r.get("nome") for r in rows}
        assert "Rossi Mario"  in nomi, f"'Rossi Mario' mancante: {nomi}"
        assert "Bianchi Anna" in nomi, f"'Bianchi Anna' mancante: {nomi}"
        assert "Verdi Luigi"  not in nomi, f"'Verdi Luigi' non doveva esserci: {nomi}"

    def test_tipi_inferiti(self, client, pg_conn, pg_table, make_project, poll):
        """
        E-02c: verifica che i tipi colonne siano inferiti correttamente.
        nome → str, data_ordine → date, importo → real
        """
        pg_table(ddl=DDL_CLIENTI, rows=ROWS_CLIENTI, table_name=TABLE_CLIENTI)
        pg_table(ddl=DDL_ORDINI,  rows=ROWS_ORDINI,  table_name=TABLE_ORDINI)

        canvas = _build_join_canvas(TABLE_CLIENTI, TABLE_ORDINI)

        project_id = make_project(
            name="[AUTO] E-02 Tipi inferiti",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        res = client.get(f"/projects/{project_id}/preview", params={"limit": 50})
        assert res.status_code == 200
        task_id = res.json().get("task_id")
        result = poll(task_id)
        assert result.get("success") is True

        columns = result.get("columns", [])
        col_map = {c["name"]: c["type"] for c in columns}

        # Tipi attesi per le colonne visibili
        assert col_map.get("nome")        == "str",  f"nome: atteso str, trovato {col_map.get('nome')}"
        assert col_map.get("data_ordine") == "date", f"data_ordine: atteso date, trovato {col_map.get('data_ordine')}"
        assert col_map.get("importo")     == "real", f"importo: atteso real, trovato {col_map.get('importo')}"

        # Colonne hidden non devono comparire
        assert "cliente_id" not in col_map, "cliente_id non doveva essere visibile"
