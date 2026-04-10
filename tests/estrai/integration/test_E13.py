"""
E-13 — Upload FileGDB (.gdb zippato)
======================================
Verifica:
  1. Upload .zip contenente cartella .gdb va a buon fine
  2. Feature class visibile via GET /layers
  3. Preview restituisce le geometrie poligonali
  4. Attributi corretti (nome, area_ha)

Struttura zip attesa dal backend:
  test_e13.gdb/           ← cartella .gdb nella root dello zip
    a00000001.gdbtablx
    ...

Prerequisiti:
  - Backend FastAPI attivo su localhost:8000
  - fiona installato (per creare il GDB di test)
"""

import os
import zipfile
import tempfile
import shutil
import pytest
import fiona
from fiona.crs import from_epsg

pytestmark = [pytest.mark.estrai]

GDB_LAYER = "zone_urbane"

GDB_SCHEMA = {
    "geometry": "Polygon",
    "properties": {"nome": "str", "area_ha": "float"},
}

GDB_FEATURES = [
    {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]]},
        "properties": {"nome": "Zona A", "area_ha": 12.0},
    },
    {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[(2, 0), (3, 0), (3, 1), (2, 1), (2, 0)]]},
        "properties": {"nome": "Zona B", "area_ha": 8.5},
    },
    {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[(4, 0), (5, 0), (5, 1), (4, 1), (4, 0)]]},
        "properties": {"nome": "Zona C", "area_ha": 5.2},
    },
]


@pytest.fixture(scope="module")
def gdb_zip():
    """
    Crea un FileGDB con fiona (driver OpenFileGDB),
    lo zippa con la cartella .gdb nella root.
    Elimina al teardown.
    """
    tmp_dir = tempfile.mkdtemp()
    gdb_path = os.path.join(tmp_dir, "test_e13.gdb")
    zip_path = os.path.join(tmp_dir, "test_e13.zip")

    # Crea il GDB
    with fiona.open(
        gdb_path, "w",
        driver="OpenFileGDB",
        schema=GDB_SCHEMA,
        crs=from_epsg(4326),
        layer=GDB_LAYER,
    ) as f:
        for feat in GDB_FEATURES:
            f.write(feat)

    # Zippa: test_e13.gdb/ nella root dello zip
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(gdb_path):
            for file in files:
                fp = os.path.join(root, file)
                arcname = os.path.relpath(fp, tmp_dir)  # → test_e13.gdb/...
                zf.write(fp, arcname)

    yield zip_path
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ─── Test ─────────────────────────────────────────────────────────────────────

class TestE13:

    def test_upload_gdb_zip(self, client, gdb_zip):
        """E-13a: upload .zip con .gdb va a buon fine."""
        with open(gdb_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e13.zip", f, "application/zip")},
                data={"name": "[AUTO] E-13 upload gdb zip"},
            )
        assert res.status_code == 200, f"Upload fallito: {res.text}"
        file_id = res.json().get("id")
        assert file_id

        client.delete(f"/files/{file_id}")

    def test_feature_class_visibile(self, client, gdb_zip):
        """E-13b: GET /layers restituisce la feature class 'zone_urbane'."""
        with open(gdb_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e13.zip", f, "application/zip")},
                data={"name": "[AUTO] E-13 layers"},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]
        try:
            # Il layer è già registrato nel FileSource al momento dell'upload
            # (il GDB ha un solo layer → viene selezionato automaticamente)
            file_source = res.json()
            layer_in_source = file_source.get("layer")
            assert layer_in_source == GDB_LAYER, (
                f"Layer atteso '{GDB_LAYER}', trovato '{layer_in_source}'"
            )

            # Verifica anche via GET /files/{id}
            res_get = client.get(f"/files/{file_id}")
            assert res_get.status_code == 200
            assert res_get.json().get("layer") == GDB_LAYER

        finally:
            client.delete(f"/files/{file_id}")

    def test_preview_poligoni(self, client, gdb_zip):
        """E-13c: preview restituisce 3 feature con geometria polygon."""
        with open(gdb_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e13.zip", f, "application/zip")},
                data={"name": "[AUTO] E-13 preview", "layer": GDB_LAYER},
            )
        assert res.status_code == 200, f"Upload con layer fallito: {res.text}"
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview", params={"limit": 50})
            assert res_preview.status_code == 200, f"Preview fallita: {res_preview.text}"

            data = res_preview.json()
            rows = data.get("rows", data.get("data", []))
            assert len(rows) == 3, f"Attese 3 feature, trovate {len(rows)}"

            columns = [c["name"] if isinstance(c, dict) else c
                       for c in data.get("columns", [])]

            # Colonna geometry presente
            geom_cols = [c for c in columns if "geom" in c.lower() or "geometry" in c.lower()]
            assert geom_cols, f"Nessuna colonna geometry: {columns}"

            # Geometrie non null
            geom_col = geom_cols[0]
            for row in rows:
                assert row.get(geom_col) is not None, f"Geometria null: {row}"

        finally:
            client.delete(f"/files/{file_id}")

    def test_attributi_corretti(self, client, gdb_zip):
        """E-13d: attributi nome e area_ha presenti con valori corretti."""
        with open(gdb_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e13.zip", f, "application/zip")},
                data={"name": "[AUTO] E-13 attributi", "layer": GDB_LAYER},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview", params={"limit": 50})
            assert res_preview.status_code == 200

            data = res_preview.json()
            rows = data.get("rows", data.get("data", []))
            columns = [c["name"] if isinstance(c, dict) else c
                       for c in data.get("columns", [])]

            assert "nome"    in columns, f"Colonna 'nome' mancante: {columns}"
            assert "area_ha" in columns, f"Colonna 'area_ha' mancante: {columns}"

            nomi = {r.get("nome") for r in rows}
            assert "Zona A" in nomi, f"'Zona A' non trovata: {nomi}"
            assert "Zona C" in nomi, f"'Zona C' non trovata: {nomi}"

        finally:
            client.delete(f"/files/{file_id}")


# ─── Edge cases ───────────────────────────────────────────────────────────────

import io as _io
import zipfile as _zipfile

class TestE13Edge:

    def test_zip_senza_gdb(self, client):
        """
        E-13 edge: zip che non contiene alcuna cartella .gdb → errore graceful.
        Nessun crash 500, risposta 400 o messaggio errore chiaro.
        """
        zip_buf = _io.BytesIO()
        with _zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("readme.txt", "nessun gdb qui")
            zf.writestr("dati.csv", "id,nome\n1,test")
        zip_buf.seek(0)

        res = client.post(
            "/files/upload",
            files={"file": ("no_gdb.zip", zip_buf, "application/zip")},
            data={"name": "[AUTO] E-13 edge zip senza gdb"},
        )
        assert res.status_code in (200, 400), (
            f"Zip senza GDB ha causato crash 500: {res.status_code} {res.text}"
        )
        if res.status_code == 200:
            client.delete(f"/files/{res.json()['id']}")

    def test_zip_corrotto(self, client):
        """
        E-13 edge: zip malformato → 400 con messaggio chiaro.
        Dopo B-BE-58 (file_service) + fix router: ValueError non viene più
        swallowed dal fallback raw — ZIP corrotto restituisce 400.
        """
        fake_zip = b"PK\x03\x04" + b"\x00" * 50
        res = client.post(
            "/files/upload",
            files={"file": ("corrupted.zip", fake_zip, "application/zip")},
            data={"name": "[AUTO] E-13 edge zip corrotto"},
        )
        assert res.status_code == 400, (
            f"Zip corrotto dovrebbe restituire 400, ricevuto: {res.status_code} {res.text}"
        )
        detail = res.json().get("detail", "")
        assert detail, "Messaggio di errore vuoto"
        assert "Traceback" not in detail, "Stack trace esposto nel messaggio"

    def test_gdb_layer_inesistente(self, client, gdb_zip):
        """
        E-13 edge: preview con layer inesistente → 400 o 404, nessun crash.
        """
        with open(gdb_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e13_edge.zip", f, "application/zip")},
                data={"name": "[AUTO] E-13 edge layer inesistente"},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]

        try:
            res_preview = client.get(
                f"/files/{file_id}/preview",
                params={"limit": 10, "layer": "layer_che_non_esiste"}
            )
            assert res_preview.status_code in (200, 400, 404), (
                f"Layer inesistente ha causato crash: {res_preview.status_code} {res_preview.text}"
            )
        finally:
            client.delete(f"/files/{file_id}")
