"""
M-01 — Distinct su dataset reale
==================================
Verifica che il nodo Distinct rimuova correttamente i duplicati.

Dataset sintetico: 6819 righe con ~391 duplicati intenzionali
Righe attese dopo distinct: 6428

Test A (file_source): CSV caricato come file sorgente
Test B (project_source): progetto ESTRAI eseguito come sorgente

Prerequisiti:
  - Backend FastAPI attivo su localhost:8000
"""

import pytest
from conftest import build_manipola_canvas

pytestmark = [pytest.mark.manipola]

ROWS_TOTALI  = 6819
ROWS_UNICHE  = 6428


class TestM01:

    def test_distinct_via_file_source(self, client, make_project, poll, csv_immobili):
        """
        M-01a: Distinct su CSV caricato come file_source.
        6819 righe → 6428 righe uniche (subset=None, keep=first).
        """
        canvas = build_manipola_canvas(
            file_source_id=csv_immobili,
            transform_op="distinct",
            transform_config={"subset": None, "keep": "first"},
        )

        project_id = make_project(
            name="[AUTO] M-01a distinct file_source",
            project_type="manipola",
            canvas=canvas,
        )

        res = client.post(f"/projects/{project_id}/execute")
        assert res.status_code == 200
        task_id = res.json().get("task_id")
        result = poll(task_id)

        assert result.get("success") is True, f"Distinct fallito: {result.get('message')}"
        row_count = result.get("row_count", result.get("rows_count"))
        assert row_count == ROWS_UNICHE, (
            f"Righe attese {ROWS_UNICHE}, trovate {row_count}"
        )

    def test_distinct_row_count_ridotto(self, client, make_project, poll, csv_immobili):
        """
        M-01b: verifica via preview che le righe siano effettivamente ridotte.
        total_count dal preview deve essere < ROWS_TOTALI.
        """
        canvas = build_manipola_canvas(
            file_source_id=csv_immobili,
            transform_op="distinct",
            transform_config={"subset": None, "keep": "first"},
        )

        project_id = make_project(
            name="[AUTO] M-01b distinct preview",
            project_type="manipola",
            canvas=canvas,
        )

        res = client.get(f"/projects/{project_id}/preview", params={"limit": 100})
        assert res.status_code == 200
        task_id = res.json().get("task_id")
        result = poll(task_id)

        assert result.get("success") is True
        total = result.get("total_count", 0)
        assert total < ROWS_TOTALI, (
            f"Distinct non ha ridotto le righe: {total} >= {ROWS_TOTALI}"
        )
        assert total == ROWS_UNICHE, (
            f"total_count atteso {ROWS_UNICHE}, trovato {total}"
        )

    def test_distinct_subset_colonne(self, client, make_project, poll, csv_immobili):
        """
        M-01c: Distinct su subset di colonne (comune, zona, tipo).
        Riduce ulteriormente rispetto al distinct su tutte le colonne.
        """
        canvas = build_manipola_canvas(
            file_source_id=csv_immobili,
            transform_op="distinct",
            transform_config={"subset": ["comune", "zona", "tipo"], "keep": "first"},
        )

        project_id = make_project(
            name="[AUTO] M-01c distinct subset",
            project_type="manipola",
            canvas=canvas,
        )

        res = client.get(f"/projects/{project_id}/preview", params={"limit": 10})
        assert res.status_code == 200
        task_id = res.json().get("task_id")
        result = poll(task_id)

        assert result.get("success") is True
        total = result.get("total_count", 0)
        # 8 comuni × 7 zone × 5 tipi = 280 combinazioni al massimo
        assert total <= 280, f"Distinct subset: troppi risultati ({total}), max atteso 280"
        assert total > 0, "Distinct subset: nessuna riga"

    def test_distinct_via_project_source(self, client, make_project, poll,
                                          pg_conn, pg_table):
        """
        M-01d: Distinct su sorgente progetto ESTRAI (project_source).
        Crea tabella PG con duplicati, esegue ESTRAI, poi MANIPOLA distinct.
        """
        TABLE = "test_m01_distinct"
        pg_table(
            ddl="id INT, comune VARCHAR(50), zona VARCHAR(10), valore FLOAT",
            rows=[
                f"INSERT INTO {TABLE} VALUES (1, 'Roma', 'A', 1000.0)",
                f"INSERT INTO {TABLE} VALUES (2, 'Roma', 'A', 1000.0)",   # duplicato
                f"INSERT INTO {TABLE} VALUES (3, 'Milano', 'B', 2000.0)",
                f"INSERT INTO {TABLE} VALUES (4, 'Milano', 'B', 2000.0)", # duplicato
                f"INSERT INTO {TABLE} VALUES (5, 'Napoli', 'C', 1500.0)",
            ],
            table_name=TABLE,
        )

        # Crea e esegui progetto ESTRAI
        estrai_canvas = {
            "nodes": [
                {
                    "id": "src",
                    "type": "source",
                    "position": {"x": 100, "y": 200},
                    "table_name": TABLE,
                    "columns": [
                        {"name": "id",      "type": "int",  "visible": True},
                        {"name": "comune",  "type": "str",  "visible": True},
                        {"name": "zona",    "type": "str",  "visible": True},
                        {"name": "valore",  "type": "real", "visible": True},
                    ],
                    "filters": [],
                },
                {"id": "res", "type": "result", "position": {"x": 500, "y": 200},
                 "fileName": "estrai_m01", "format": "csv"},
            ],
            "edges": [{"id": "e1", "source": "src", "target": "res"}],
        }

        estrai_id = make_project(
            name="[AUTO] M-01d ESTRAI sorgente",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=estrai_canvas,
        )

        # Esegui ESTRAI
        res_ex = client.post(f"/projects/{estrai_id}/execute")
        assert res_ex.status_code == 200
        poll(res_ex.json().get("task_id"))

        # Crea MANIPOLA con project_source
        manipola_canvas = {
            "nodes": [
                {
                    "id": "node-src",
                    "type": "project_source",
                    "position": {"x": 100, "y": 200},
                    "source_project_id": estrai_id,
                },
                {
                    "id": "node-transform",
                    "type": "transform",
                    "position": {"x": 400, "y": 200},
                    "transform_op": "distinct",
                    "config": {"subset": ["comune", "zona", "valore"], "keep": "first"},
                },
                {
                    "id": "node-result",
                    "type": "result",
                    "position": {"x": 700, "y": 200},
                    "fileName": "distinct_m01d",
                    "format": "csv",
                },
            ],
            "edges": [
                {"id": "e1", "source": "node-src",       "target": "node-transform"},
                {"id": "e2", "source": "node-transform",  "target": "node-result"},
            ],
        }

        manip_id = make_project(
            name="[AUTO] M-01d MANIPOLA distinct project_source",
            project_type="manipola",
            canvas=manipola_canvas,
        )

        res = client.post(f"/projects/{manip_id}/execute")
        assert res.status_code == 200
        result = poll(res.json().get("task_id"))

        assert result.get("success") is True, f"M-01d fallito: {result.get('message')}"
        row_count = result.get("row_count", result.get("rows_count"))
        assert row_count == 3, (
            f"Attese 3 righe uniche (da 5 con 2 duplicati), trovate {row_count}"
        )
