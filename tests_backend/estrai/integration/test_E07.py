"""
E-07 — Query lunga → timeout graceful, server non crasha
==========================================================
Simula una query molto pesante (cross join → ~9M righe senza LIMIT)
e verifica che il backend la gestisca senza crashare.

Comportamento atteso:
  - Il task termina in SUCCESS (il backend regge) o FAILURE (errore gestito)
  - In nessun caso il server risponde 500 o smette di rispondere
  - Dopo il task il backend è ancora funzionante (health check)

Strategia:
  - 2 tabelle PG da ~3000 righe ciascuna (via generate_series)
  - Cross join senza condizione JOIN → ~9M righe
  - Nessun LIMIT sul nodo result
  - Poll con timeout lungo (120s)

Nota architetturale:
  Il DB_QUERY_TIMEOUT (30s) è configurato ma non propagato come
  statement_timeout a PostgreSQL. Il vero limite è il Celery
  soft_time_limit (3600s execute, 300s preview). La query può
  quindi completare o fallire per memoria, mai per timeout DB.
  Questo è un finding documentato dal test.

Prerequisiti:
  - Docker PostgreSQL attivo su localhost:5432
  - Backend FastAPI attivo su localhost:8000
"""

import pytest

pytestmark = [pytest.mark.estrai, pytest.mark.postgres, pytest.mark.slow]

TABLE_A = "test_e07_big_a"
TABLE_B = "test_e07_big_b"
ROWS_PER_TABLE = 3000   # 3000 × 3000 = 9M righe nel cross join


class TestE07:

    def test_query_pesante_no_crash(self, client, pg_conn, pg_table, make_project, poll):
        """
        E-07: cross join ~9M righe senza LIMIT → backend gestisce senza crash.

        Il task può completare o fallire, ma:
          - Niente 500 dal server
          - Il backend risponde ancora dopo il test
        """
        # Crea tabella A: 3000 righe via INSERT batch
        rows_a = []
        for i in range(0, ROWS_PER_TABLE, 100):
            batch = ", ".join(f"({j}, 'riga_{j}')" for j in range(i, min(i + 100, ROWS_PER_TABLE)))
            rows_a.append(f"INSERT INTO {TABLE_A} (id, valore) VALUES {batch}")

        pg_table(
            ddl="id INT, valore TEXT",
            rows=rows_a,
            table_name=TABLE_A,
        )

        # Crea tabella B: altre 3000 righe
        rows_b = []
        for i in range(0, ROWS_PER_TABLE, 100):
            batch = ", ".join(f"({j}, {j * 1.5})" for j in range(i, min(i + 100, ROWS_PER_TABLE)))
            rows_b.append(f"INSERT INTO {TABLE_B} (id, misura) VALUES {batch}")

        pg_table(
            ddl="id INT, misura REAL",
            rows=rows_b,
            table_name=TABLE_B,
        )

        # Canvas: cross join senza condizione, nessun LIMIT
        canvas = {
            "nodes": [
                {
                    "id": "node-a",
                    "type": "source",
                    "position": {"x": 50, "y": 100},
                    "table_name": TABLE_A,
                    "columns": [
                        {"name": "id",     "type": "int", "visible": True},
                        {"name": "valore", "type": "str", "visible": True},
                    ],
                    "filters": [],
                },
                {
                    "id": "node-b",
                    "type": "source",
                    "position": {"x": 50, "y": 350},
                    "table_name": TABLE_B,
                    "columns": [
                        {"name": "id",     "type": "int",  "visible": False},
                        {"name": "misura", "type": "real", "visible": True},
                    ],
                    "filters": [],
                },
                {
                    "id": "node-result",
                    "type": "result",
                    "position": {"x": 500, "y": 225},
                    "fileName": "e07_output",
                    "format": "csv",
                    # Nessun LIMIT — forza la lettura di tutte le righe
                },
            ],
            "edges": [
                # Cross join: edge senza source_column/target_column
                {
                    "id": "edge-cross",
                    "source": "node-a",
                    "target": "node-b",
                    "join_type": "inner",
                    "source_column": "",
                    "target_column": "",
                },
                {
                    "id": "edge-result",
                    "source": "node-b",
                    "target": "node-result",
                },
            ],
        }

        project_id = make_project(
            name="[AUTO] E-07 Query pesante no crash",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        # Avvia execute (non preview — vogliamo testare il task completo)
        res = client.post(f"/projects/{project_id}/execute")
        assert res.status_code == 200, f"Avvio execute fallito: {res.text}"
        task_id = res.json().get("task_id")
        assert task_id, "task_id mancante"

        # Poll con timeout lungo — può essere lento
        try:
            result = poll(task_id, timeout=120)
            # Se completa con SUCCESS: il backend ha retto tutto
            final_status = "success"
        except AssertionError as e:
            # FAILURE: il task è fallito ma in modo gestito (non un crash)
            final_status = "failure"
            error_msg = str(e)
            # Verifica che l'errore abbia un messaggio (non silenzioso)
            assert "FAILURE" in error_msg or "failure" in error_msg.lower(), (
                f"Stato inatteso: {error_msg}"
            )
        except TimeoutError:
            # La query sta ancora girando dopo 120s — non è un crash
            final_status = "timeout_poll"

        # ── Verifica critica: il server è ancora vivo ──────────────────────
        health_res = client.get("/connections")
        assert health_res.status_code == 200, (
            f"Il backend non risponde dopo la query pesante! "
            f"Status: {health_res.status_code}"
        )

        print(f"\n[E-07] Esito task: {final_status} - server ancora operativo OK")

        # Il test fallisce solo se il server non risponde —
        # SUCCESS, FAILURE e timeout_poll sono tutti esiti accettabili
        assert final_status in ("success", "failure", "timeout_poll"), (
            f"Esito inatteso: {final_status}"
        )
