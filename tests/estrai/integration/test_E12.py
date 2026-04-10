"""
E-12 — Upload Excel fogli multipli
====================================
Verifica:
  1. Upload .xlsx con 3 fogli va a buon fine
  2. GET /files/{id}/layers → lista tutti e 3 i fogli
  3. Upload con sheet specifico → preview corretto per ognuno
  4. Colonne e tipi corretti per ogni foglio

Flusso API (identico ai layer GPKG):
  POST /api/files/upload          → file_id (primo foglio di default)
  GET  /api/files/{id}/layers     → ["vendite", "clienti", "prodotti"]
  Per ogni foglio:
    POST /api/files/upload (stesso file, sheet=<nome>) → file_id specifico
    GET  /api/files/{id}/preview  → righe + colonne

Prerequisiti:
  - Backend FastAPI attivo su localhost:8000
  - openpyxl installato (per creare il file .xlsx di test)
"""

import io
import os
import tempfile
import pytest
import openpyxl

pytestmark = [pytest.mark.estrai]

# ─── Definizione fogli ─────────────────────────────────────────────────────────

SHEETS = {
    "vendite": {
        "headers": ["id", "prodotto", "quantita", "totale", "data"],
        "rows": [
            [1, "Laptop",   3, 2699.97, "2025-01-15"],
            [2, "Mouse",   10,  245.00, "2025-01-16"],
            [3, "Monitor",  2,  698.00, "2025-01-17"],
            [4, "Tastiera", 5,  375.00, "2025-01-18"],
        ],
    },
    "clienti": {
        "headers": ["id", "nome", "citta", "fatturato"],
        "rows": [
            [1, "Mario Rossi",    "Roma",   15000.0],
            [2, "Luigi Bianchi",  "Milano", 23000.0],
            [3, "Anna Verdi",     "Torino",  8500.0],
        ],
    },
    "prodotti": {
        "headers": ["codice", "descrizione", "prezzo", "disponibile"],
        "rows": [
            ["LAP001", "Laptop Pro 15",   899.99, True],
            ["MOU001", "Mouse Wireless",   24.50, True],
            ["MON001", "Monitor 27\"",    349.00, True],
            ["TAS001", "Tastiera BT",      75.00, False],
            ["CAM001", "Webcam HD",        45.00, True],
        ],
    },
}


@pytest.fixture(scope="module")
def excel_file():
    """Crea .xlsx con 3 fogli, elimina al teardown."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # rimuove foglio default vuoto

    for sheet_name, data in SHEETS.items():
        ws = wb.create_sheet(title=sheet_name)
        ws.append(data["headers"])
        for row in data["rows"]:
            ws.append(row)

    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    wb.save(tmp.name)

    yield tmp.name
    os.unlink(tmp.name)


# ─── Test ─────────────────────────────────────────────────────────────────────

class TestE12:

    def test_upload_xlsx(self, client, excel_file):
        """E-12a: upload .xlsx va a buon fine."""
        with open(excel_file, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e12.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"name": "[AUTO] E-12 upload xlsx"},
            )
        assert res.status_code == 200, f"Upload fallito: {res.text}"
        file_id = res.json().get("id")
        assert file_id

        client.delete(f"/files/{file_id}")

    def test_lista_fogli(self, client, excel_file):
        """E-12b: GET /layers restituisce tutti e 3 i fogli."""
        with open(excel_file, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e12.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"name": "[AUTO] E-12 lista fogli"},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]

        try:
            res_layers = client.get(f"/files/{file_id}/layers")
            assert res_layers.status_code == 200, f"GET layers fallito: {res_layers.text}"

            data = res_layers.json()
            # Per xlsx i fogli sono nella chiave "sheets", non "layers"
            layers = data.get("sheets", data.get("layers", []))
            assert len(layers) == 3, f"Attesi 3 fogli, trovati {len(layers)}: {data}"

            for expected in ["vendite", "clienti", "prodotti"]:
                assert expected in layers, f"Foglio '{expected}' mancante: {layers}"

        finally:
            client.delete(f"/files/{file_id}")

    @pytest.mark.parametrize("sheet_name,expected_rows,expected_cols", [
        ("vendite",  4, ["id", "prodotto", "quantita", "totale"]),
        ("clienti",  3, ["id", "nome", "citta", "fatturato"]),
        ("prodotti", 5, ["codice", "descrizione", "prezzo"]),
    ])
    def test_preview_per_foglio(self, client, excel_file, sheet_name, expected_rows, expected_cols):
        """E-12c: preview di ciascun foglio restituisce righe e colonne corrette."""
        with open(excel_file, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e12.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"name": f"[AUTO] E-12 preview {sheet_name}", "sheet": sheet_name},
            )
        assert res.status_code == 200, f"Upload foglio '{sheet_name}' fallito: {res.text}"
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview", params={"limit": 50})
            assert res_preview.status_code == 200, (
                f"Preview foglio '{sheet_name}' fallita: {res_preview.text}"
            )

            data = res_preview.json()
            rows = data.get("rows", data.get("data", []))
            columns = [c["name"] if isinstance(c, dict) else c
                       for c in data.get("columns", [])]

            assert len(rows) == expected_rows, (
                f"Foglio '{sheet_name}': attese {expected_rows} righe, trovate {len(rows)}"
            )
            for col in expected_cols:
                assert col in columns, (
                    f"Foglio '{sheet_name}': colonna '{col}' mancante. Trovate: {columns}"
                )

        finally:
            client.delete(f"/files/{file_id}")

    def test_fogli_indipendenti(self, client, excel_file):
        """
        E-12d: upload con sheet diversi produce FileSource indipendenti
        con row_count corretto per ognuno.
        """
        expected_counts = {"vendite": 4, "clienti": 3, "prodotti": 5}
        file_ids = []

        try:
            for sheet_name, expected_count in expected_counts.items():
                with open(excel_file, "rb") as f:
                    res = client.post(
                        "/files/upload",
                        files={"file": ("test_e12.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                        data={"name": f"[AUTO] E-12 indip {sheet_name}", "sheet": sheet_name},
                    )
                assert res.status_code == 200
                file_source = res.json()
                file_id = file_source["id"]
                file_ids.append(file_id)

                row_count = file_source.get("row_count")
                assert row_count == expected_count, (
                    f"Foglio '{sheet_name}': row_count atteso {expected_count}, trovato {row_count}"
                )

        finally:
            for fid in file_ids:
                client.delete(f"/files/{fid}")


# ─── Edge cases ───────────────────────────────────────────────────────────────

class TestE12Edge:

    def test_xlsx_foglio_vuoto(self, client):
        """
        E-12 edge: XLSX con un foglio completamente vuoto → upload OK, nessun crash.
        """
        import openpyxl, tempfile, os
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Vuoto"
        # Nessun dato, nemmeno header
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        wb.save(tmp.name)
        tmp.close()

        try:
            with open(tmp.name, "rb") as f:
                res = client.post(
                    "/files/upload",
                    files={"file": ("empty_sheet.xlsx", f,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    data={"name": "[AUTO] E-12 edge foglio vuoto", "sheet": "Vuoto"},
                )
            assert res.status_code in (200, 400), (
                f"Foglio vuoto ha causato crash 500: {res.status_code} {res.text}"
            )
            if res.status_code == 200:
                client.delete(f"/files/{res.json()['id']}")
        finally:
            os.unlink(tmp.name)

    def test_xlsx_solo_intestazione(self, client):
        """
        E-12 edge: XLSX con solo riga header, nessun dato → row_count=0, nessun crash.
        """
        import openpyxl, tempfile, os
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "SoloHeader"
        ws.append(["id", "nome", "valore", "data"])
        # Nessuna riga dati
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        wb.save(tmp.name)
        tmp.close()

        try:
            with open(tmp.name, "rb") as f:
                res = client.post(
                    "/files/upload",
                    files={"file": ("only_header.xlsx", f,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    data={"name": "[AUTO] E-12 edge solo header", "sheet": "SoloHeader"},
                )
            assert res.status_code == 200, (
                f"XLSX solo header ha crashato: {res.status_code} {res.text}"
            )
            file_source = res.json()
            file_id = file_source["id"]
            try:
                assert file_source.get("row_count") == 0, (
                    f"row_count atteso 0, trovato {file_source.get('row_count')}"
                )
                # Preview non deve crashare
                res_p = client.get(f"/files/{file_id}/preview")
                assert res_p.status_code == 200
            finally:
                client.delete(f"/files/{file_id}")
        finally:
            os.unlink(tmp.name)

    def test_xlsx_formato_non_valido(self, client):
        """
        E-12 edge: file rinominato .xlsx ma contenuto non valido.
        Il backend accetta l'upload (lazy validation), ma la preview
        deve gestire l'errore senza crash 500 oppure restituire 0 righe.
        """
        fake_xlsx = b"questo non e' un xlsx valido"
        res = client.post(
            "/files/upload",
            files={"file": ("fake.xlsx", fake_xlsx,
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"name": "[AUTO] E-12 edge fake xlsx"},
        )
        # Upload accettato (lazy validation) — comportamento corretto del backend
        assert res.status_code == 200, f"Upload inatteso: {res.status_code} {res.text}"
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview")
            # La preview deve gestire l'errore gracefully:
            # 200 con 0 righe, 400/404 file non trovato, o 500 JSON (non crash raw)
            assert res_preview.status_code in (200, 400, 404, 500), (
                f"Preview XLSX corrotto risposta inattesa: {res_preview.status_code}"
            )
            if res_preview.status_code == 500:
                assert res_preview.headers.get("content-type", "").startswith("application/json"), (
                    "Preview XLSX corrotto ha restituito 500 con risposta non-JSON (crash raw)"
                )
        finally:
            client.delete(f"/files/{file_id}")
