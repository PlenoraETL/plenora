"""
E-14 — Upload file > 50MB (Shapefile 80MB)
===========================================
Verifica che l'upload di file grandi funzioni senza timeout o errori.

File generato:
  - Shapefile con 350.000 punti (area Italia)
  - Dimensione non compressa: ~86 MB
  - Dimensione ZIP inviato: ~38 MB
  - row_count atteso: 350.000

Verifica:
  1. Upload completato con 200 (nessun timeout, nessun 413)
  2. row_count = 350.000
  3. Colonne corrette (id, nome, descr, valore, geometry)
  4. Preview funziona (prime 100 righe)

Nota: MAX_FILE_SIZE è configurato a 100MB nel backend ma non enforced.
Il test verifica il comportamento reale — non deve crashare.

Prerequisiti:
  - Backend FastAPI attivo su localhost:8000
  - fiona installato
"""

import os
import string
import random
import zipfile
import tempfile
import shutil
import pytest
import fiona
from fiona.crs import from_epsg

pytestmark = [pytest.mark.estrai, pytest.mark.slow]

N_ROWS = 350_000
RANDOM_SEED = 42


@pytest.fixture(scope="module")
def large_shapefile_zip():
    """
    Genera shapefile con 350k punti (~86MB non compresso, ~38MB zip).
    Richiede ~30s di generazione.
    """
    print(f"\n[E-14] Generazione shapefile {N_ROWS:,} righe (~86MB)...")
    schema = {
        "geometry": "Point",
        "properties": {
            "id":     "int",
            "nome":   "str",
            "descr":  "str",
            "valore": "float",
        },
    }

    tmp_dir = tempfile.mkdtemp()
    shp_path = os.path.join(tmp_dir, "large_test.shp")

    random.seed(RANDOM_SEED)
    with fiona.open(shp_path, "w", driver="ESRI Shapefile",
                    schema=schema, crs=from_epsg(4326)) as f:
        for i in range(N_ROWS):
            lon = 6.0 + random.random() * 12.0
            lat = 36.0 + random.random() * 11.0
            f.write({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id":     i,
                    "nome":   f"punto_{i:06d}",
                    "descr":  "".join(random.choices(string.ascii_letters, k=100)),
                    "valore": round(random.random() * 10000, 2),
                },
            })

    zip_path = os.path.join(tmp_dir, "large_test.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in os.listdir(tmp_dir):
            if fn != "large_test.zip":
                zf.write(os.path.join(tmp_dir, fn), fn)

    zip_size_mb = os.path.getsize(zip_path) / 1024 / 1024
    print(f"[E-14] ZIP pronto: {zip_size_mb:.1f} MB -> {zip_path}")

    yield zip_path
    shutil.rmtree(tmp_dir, ignore_errors=True)


class TestE14:

    def test_upload_file_grande(self, client, large_shapefile_zip):
        """
        E-14a: upload shapefile ~86MB (zip ~38MB) completato senza errori.
        Nessun 413, nessun timeout, nessun 500.
        """
        zip_size_mb = os.path.getsize(large_shapefile_zip) / 1024 / 1024
        print(f"\n[E-14] Upload {zip_size_mb:.1f} MB...")

        with open(large_shapefile_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("large_test.zip", f, "application/zip")},
                data={"name": "[AUTO] E-14 large shapefile"},
            )

        assert res.status_code == 200, (
            f"Upload file grande fallito ({zip_size_mb:.1f} MB): "
            f"status={res.status_code}, body={res.text[:300]}"
        )
        file_id = res.json().get("id")
        assert file_id

        client.delete(f"/files/{file_id}")

    def test_row_count_350k(self, client, large_shapefile_zip):
        """E-14b: row_count = 350.000."""
        with open(large_shapefile_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("large_test.zip", f, "application/zip")},
                data={"name": "[AUTO] E-14 row count"},
            )
        assert res.status_code == 200
        file_source = res.json()
        file_id = file_source["id"]

        try:
            row_count = file_source.get("row_count")
            assert row_count == N_ROWS, (
                f"row_count atteso {N_ROWS:,}, trovato {row_count:,}"
            )
        finally:
            client.delete(f"/files/{file_id}")

    def test_preview_file_grande(self, client, large_shapefile_zip):
        """E-14c: preview restituisce 100 righe senza crash."""
        with open(large_shapefile_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("large_test.zip", f, "application/zip")},
                data={"name": "[AUTO] E-14 preview"},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview", params={"limit": 100})
            assert res_preview.status_code == 200, (
                f"Preview file grande fallita: {res_preview.text[:200]}"
            )

            data = res_preview.json()
            rows = data.get("rows", data.get("data", []))
            assert len(rows) == 100, f"Attese 100 righe, trovate {len(rows)}"

            columns = [c["name"] if isinstance(c, dict) else c
                       for c in data.get("columns", [])]
            for col in ["id", "nome", "valore"]:
                assert col in columns, f"Colonna '{col}' mancante: {columns}"

        finally:
            client.delete(f"/files/{file_id}")
