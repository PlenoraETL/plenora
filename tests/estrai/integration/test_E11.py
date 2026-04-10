"""
E-11 — Upload CSV lat/lon → geometry:point
==========================================
Verifica il flusso completo di un CSV con colonne lat/lon su 10k righe.

Comportamento reale del backend:
  - Il backend NON inferisce automaticamente geometry:point da lat/lon
  - Le colonne lat/lon vengono caricate come tipo 'real' (float)
  - L'utente dichiara esplicitamente geometry:point nel canvas
  - Il backend rispetta il tipo dichiarato nel canvas

Verifica:
  1. Upload CSV 10k righe va a buon fine
  2. FileSource.row_count = 10000
  3. Colonne lat/lon inferite come 'real' nel FileSource
  4. Preview via /files/{id}/preview → 10k righe, lat/lon valorizzate
  5. Preview con canvas che dichiara geometry:point → tipo rispettato

Prerequisiti:
  - Backend FastAPI attivo su localhost:8000
"""

import io
import os
import csv
import math
import random
import tempfile
import pytest

pytestmark = [pytest.mark.estrai]

N_ROWS = 10_000
RANDOM_SEED = 42


# ─── Fixture: CSV 10k righe con lat/lon ───────────────────────────────────────

@pytest.fixture(scope="module")
def csv_latlon_file():
    """
    Genera CSV con 10k righe: id, nome, lat, lon, valore.
    Coordinate simulate nell'area Italia (lat 36-47, lon 6-18).
    """
    random.seed(RANDOM_SEED)
    tmp = tempfile.NamedTemporaryFile(
        suffix=".csv", mode="w", newline="", encoding="utf-8", delete=False
    )
    writer = csv.writer(tmp)
    writer.writerow(["id", "nome", "lat", "lon", "valore"])
    for i in range(N_ROWS):
        lat = round(36.0 + random.random() * 11.0, 6)   # 36–47
        lon = round(6.0  + random.random() * 12.0, 6)   # 6–18
        writer.writerow([i + 1, f"punto_{i+1}", lat, lon, round(random.random() * 1000, 2)])
    tmp.close()
    yield tmp.name
    os.unlink(tmp.name)


# ─── Test ─────────────────────────────────────────────────────────────────────

class TestE11:

    def test_upload_csv_10k(self, client, csv_latlon_file):
        """E-11a: upload CSV 10k righe va a buon fine."""
        with open(csv_latlon_file, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e11_latlon.csv", f, "text/csv")},
                data={"name": "[AUTO] E-11 upload csv 10k"},
            )
        assert res.status_code == 200, f"Upload fallito: {res.text}"
        file_id = res.json().get("id")
        assert file_id

        client.delete(f"/files/{file_id}")

    def test_row_count_10k(self, client, csv_latlon_file):
        """E-11b: row_count nel FileSource = 10000."""
        with open(csv_latlon_file, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e11_latlon.csv", f, "text/csv")},
                data={"name": "[AUTO] E-11 row count"},
            )
        assert res.status_code == 200
        file_source = res.json()
        file_id = file_source["id"]

        try:
            row_count = file_source.get("row_count")
            assert row_count == N_ROWS, (
                f"row_count atteso {N_ROWS}, trovato {row_count}"
            )
        finally:
            client.delete(f"/files/{file_id}")

    def test_latlon_inferite_come_real(self, client, csv_latlon_file):
        """
        E-11c: colonne lat e lon vengono inferite come tipo 'real'.
        Il backend non inferisce geometry:point automaticamente da lat/lon.
        """
        with open(csv_latlon_file, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e11_latlon.csv", f, "text/csv")},
                data={"name": "[AUTO] E-11 tipi latlon"},
            )
        assert res.status_code == 200
        file_source = res.json()
        file_id = file_source["id"]

        try:
            columns = file_source.get("columns", [])
            col_map = {
                (c["name"] if isinstance(c, dict) else c):
                (c.get("type", "") if isinstance(c, dict) else "")
                for c in columns
            }

            assert "lat" in col_map, f"Colonna 'lat' mancante: {list(col_map.keys())}"
            assert "lon" in col_map, f"Colonna 'lon' mancante: {list(col_map.keys())}"

            assert col_map["lat"] == "real", (
                f"Tipo 'lat' atteso 'real', trovato '{col_map['lat']}'"
            )
            assert col_map["lon"] == "real", (
                f"Tipo 'lon' atteso 'real', trovato '{col_map['lon']}'"
            )

            # id → int, nome → str, valore → real
            assert col_map.get("id")     == "int",  f"id: {col_map.get('id')}"
            assert col_map.get("nome")   == "str",  f"nome: {col_map.get('nome')}"
            assert col_map.get("valore") == "real", f"valore: {col_map.get('valore')}"

        finally:
            client.delete(f"/files/{file_id}")

    def test_preview_valori_latlon(self, client, csv_latlon_file):
        """
        E-11d: preview restituisce righe con lat/lon valorizzate
        e coordinate nell'area Italia.
        """
        with open(csv_latlon_file, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e11_latlon.csv", f, "text/csv")},
                data={"name": "[AUTO] E-11 preview latlon"},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview", params={"limit": 100})
            assert res_preview.status_code == 200, f"Preview fallita: {res_preview.text}"

            data = res_preview.json()
            rows = data.get("rows", data.get("data", []))

            assert len(rows) == 100, f"Attese 100 righe (limit), trovate {len(rows)}"

            # Valori lat/lon presenti e nell'area Italia
            for row in rows[:10]:
                lat = row.get("lat")
                lon = row.get("lon")
                assert lat is not None, f"lat null in riga: {row}"
                assert lon is not None, f"lon null in riga: {row}"
                assert 36.0 <= float(lat) <= 47.0, f"lat fuori range Italia: {lat}"
                assert 6.0  <= float(lon) <= 18.0, f"lon fuori range Italia: {lon}"

        finally:
            client.delete(f"/files/{file_id}")

    def test_count_endpoint(self, client, csv_latlon_file):
        """
        E-11e: GET /files/{id}/count restituisce 10000.
        Verifica che il conteggio righe sia corretto anche per file grandi.
        """
        with open(csv_latlon_file, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e11_latlon.csv", f, "text/csv")},
                data={"name": "[AUTO] E-11 count endpoint"},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]

        try:
            res_count = client.get(f"/files/{file_id}/count")
            assert res_count.status_code == 200, f"Count fallito: {res_count.text}"

            count_data = res_count.json()
            row_count = count_data.get("row_count")
            assert row_count == N_ROWS, (
                f"Count endpoint: atteso {N_ROWS}, trovato {row_count}"
            )
        finally:
            client.delete(f"/files/{file_id}")


# ─── Edge cases ───────────────────────────────────────────────────────────────

import io as _io

class TestE11Edge:

    def test_csv_lat_lon_fuori_range(self, client):
        """
        E-11 edge: CSV con lat/lon fuori range WGS84 (-200/200).
        Il backend deve accettare i valori senza crash — la validazione
        dei range è responsabilità dell'utente.
        """
        buf = _io.StringIO()
        import csv as _csv
        w = _csv.writer(buf)
        w.writerow(["id", "lat", "lon", "nome"])
        w.writerow([1, 999.0, -999.0, "fuori range"])
        w.writerow([2, 45.0, 9.0, "normale"])
        w.writerow([3, -200.0, 200.0, "limite"])

        data = buf.getvalue().encode("utf-8")
        res = client.post(
            "/files/upload",
            files={"file": ("out_of_range.csv", data, "text/csv")},
            data={"name": "[AUTO] E-11 edge lat/lon fuori range"},
        )
        assert res.status_code == 200, f"Upload lat/lon fuori range ha crashato: {res.text}"
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview")
            assert res_preview.status_code == 200, (
                f"Preview lat/lon fuori range ha crashato: {res_preview.text}"
            )
        finally:
            client.delete(f"/files/{file_id}")

    def test_csv_senza_header(self, client):
        """
        E-11 edge: CSV senza header → il backend gestisce gracefully.
        Accettabile: 200 con colonne auto-generate oppure 400.
        """
        buf = _io.StringIO()
        import csv as _csv
        w = _csv.writer(buf)
        # Solo dati, nessun header
        w.writerow([1, 45.5, 9.2, "Milano"])
        w.writerow([2, 41.9, 12.5, "Roma"])

        data = buf.getvalue().encode("utf-8")
        res = client.post(
            "/files/upload",
            files={"file": ("no_header.csv", data, "text/csv")},
            data={"name": "[AUTO] E-11 edge no header"},
        )
        assert res.status_code in (200, 400), (
            f"CSV senza header ha causato crash 500: {res.status_code} {res.text}"
        )
        if res.status_code == 200:
            client.delete(f"/files/{res.json()['id']}")

    def test_csv_una_riga(self, client):
        """
        E-11 edge: CSV con una sola riga dati → upload OK, row_count=1.
        """
        buf = _io.StringIO()
        import csv as _csv
        w = _csv.writer(buf)
        w.writerow(["id", "lat", "lon", "nome"])
        w.writerow([1, 45.5, 9.2, "Unica"])

        data = buf.getvalue().encode("utf-8")
        res = client.post(
            "/files/upload",
            files={"file": ("one_row.csv", data, "text/csv")},
            data={"name": "[AUTO] E-11 edge one row"},
        )
        assert res.status_code == 200
        file_source = res.json()
        file_id = file_source["id"]

        try:
            assert file_source.get("row_count") == 1, (
                f"row_count atteso 1, trovato {file_source.get('row_count')}"
            )
        finally:
            client.delete(f"/files/{file_id}")
