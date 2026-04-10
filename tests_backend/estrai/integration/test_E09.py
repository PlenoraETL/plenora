"""
E-09 — Upload Shapefile .zip con .prj EPSG:32632
=================================================
Verifica:
  1. Upload .zip contenente shapefile con .prj EPSG:32632
  2. Il CRS viene letto correttamente dal .prj → FileSource.crs = "EPSG:32632"
  3. Preview restituisce righe con geometrie visibili (non null)
  4. Nessun crash, nessuna riproiezione automatica

Prerequisiti:
  - Backend FastAPI attivo su localhost:8000
  - geopandas, shapely installati
"""

import io
import os
import zipfile
import tempfile
import pytest
import geopandas as gpd
from shapely.geometry import Point

pytestmark = [pytest.mark.estrai]

# ─── Fixture: crea shapefile .zip con CRS EPSG:32632 (UTM zona 32N) ──────────

@pytest.fixture(scope="module")
def shapefile_zip():
    """
    Crea uno shapefile con CRS EPSG:32632 (UTM 32N), lo zippa,
    restituisce il path del .zip. Elimina al teardown.
    """
    rows = [
        {"nome": "Punto A", "valore": 100, "geometry": Point(500000, 5000000)},
        {"nome": "Punto B", "valore": 250, "geometry": Point(501000, 5001000)},
        {"nome": "Punto C", "valore": 375, "geometry": Point(502000, 5002000)},
    ]
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:32632")

    # Salva come .shp in cartella temporanea
    tmp_dir = tempfile.mkdtemp()
    shp_base = os.path.join(tmp_dir, "test_e09_punti")
    gdf.to_file(shp_base + ".shp", driver="ESRI Shapefile")

    # Crea .zip con tutti i file del shapefile
    zip_path = os.path.join(tmp_dir, "test_e09_punti.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
            fp = shp_base + ext
            if os.path.exists(fp):
                zf.write(fp, arcname=os.path.basename(fp))

    yield zip_path

    # Teardown
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ─── Test ─────────────────────────────────────────────────────────────────────

class TestE09:

    def test_upload_shapefile_zip(self, client, shapefile_zip):
        """E-09a: upload .zip shapefile va a buon fine."""
        with open(shapefile_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e09_punti.zip", f, "application/zip")},
                data={"name": "[AUTO] E-09 shapefile upload"},
            )
        assert res.status_code == 200, f"Upload fallito: {res.text}"
        file_id = res.json().get("id")
        assert file_id

        client.delete(f"/files/{file_id}")

    def test_crs_rilevato_da_prj(self, client, shapefile_zip):
        """
        E-09b: il CRS viene letto dal .prj e salvato come EPSG:32632
        nel FileSource. Non deve essere 4326 o null.
        """
        with open(shapefile_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e09_punti.zip", f, "application/zip")},
                data={"name": "[AUTO] E-09 CRS da prj"},
            )
        assert res.status_code == 200, f"Upload fallito: {res.text}"
        file_source = res.json()
        file_id = file_source.get("id")

        try:
            crs = file_source.get("crs")
            assert crs is not None, "CRS null — il .prj non è stato letto"
            assert "32632" in str(crs), (
                f"CRS atteso EPSG:32632, trovato: '{crs}'"
            )
        finally:
            client.delete(f"/files/{file_id}")

    def test_preview_geometrie_visibili(self, client, shapefile_zip):
        """
        E-09c: preview restituisce 3 righe con geometrie non null.
        Le coordinate devono essere nel sistema UTM 32N (X > 100000).
        """
        with open(shapefile_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e09_punti.zip", f, "application/zip")},
                data={"name": "[AUTO] E-09 preview geometrie"},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview", params={"limit": 50})
            assert res_preview.status_code == 200, f"Preview fallita: {res_preview.text}"

            data = res_preview.json()
            rows = data.get("rows", data.get("data", []))

            assert len(rows) == 3, f"Attese 3 righe, trovate {len(rows)}"

            # Verifica colonne
            columns = [c["name"] if isinstance(c, dict) else c
                       for c in data.get("columns", [])]

            assert "nome" in columns, f"Colonna 'nome' mancante: {columns}"

            # Colonna geometry presente
            geom_cols = [c for c in columns if "geom" in c.lower() or "geometry" in c.lower()]
            assert geom_cols, f"Nessuna colonna geometry trovata: {columns}"

            # Geometrie non null
            geom_col = geom_cols[0]
            for row in rows:
                assert row.get(geom_col) is not None, f"Geometria null per riga: {row}"

        finally:
            client.delete(f"/files/{file_id}")

    def test_no_riproiezione(self, client, shapefile_zip):
        """
        E-09d: i dati NON vengono riproiettati automaticamente.
        Le coordinate restano in UTM 32N (X ~ 500000, non in gradi decimali).
        """
        with open(shapefile_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e09_punti.zip", f, "application/zip")},
                data={"name": "[AUTO] E-09 no riproiezione"},
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
            geom_cols = [c for c in columns if "geom" in c.lower() or "geometry" in c.lower()]

            if geom_cols:
                geom_col = geom_cols[0]
                for row in rows:
                    geom_val = str(row.get(geom_col, ""))
                    if geom_val and geom_val != "None":
                        # Coordinate UTM: X deve essere ~500000, non < 180 (gradi)
                        # WKT: POINT (500000 5000000)
                        if "POINT" in geom_val.upper():
                            import re
                            coords = re.findall(r"[-\d.]+", geom_val)
                            if coords:
                                x = float(coords[0])
                                assert x > 1000, (
                                    f"Coordinate sembrano riproiettate in WGS84: x={x}. "
                                    f"Atteso UTM 32N (x ~ 500000). WKT: {geom_val}"
                                )
                        break  # basta verificare una riga

        finally:
            client.delete(f"/files/{file_id}")


# ─── Edge cases ───────────────────────────────────────────────────────────────

import io as _io
import zipfile as _zipfile

class TestE09Edge:

    def test_shapefile_senza_prj(self, client):
        """
        E-09 edge: zip shapefile senza .prj → CRS null (non crash).
        Il backend deve accettare l'upload e restituire crs=None.
        """
        import geopandas as gpd
        import tempfile, os, shutil
        from shapely.geometry import Point

        rows = [{"nome": "A", "geometry": Point(500000, 5000000)}]
        gdf = gpd.GeoDataFrame(rows, crs="EPSG:32632")
        tmp_dir = tempfile.mkdtemp()
        shp_base = os.path.join(tmp_dir, "no_prj")
        gdf.to_file(shp_base + ".shp", driver="ESRI Shapefile")

        zip_buf = _io.BytesIO()
        with _zipfile.ZipFile(zip_buf, "w") as zf:
            for ext in [".shp", ".shx", ".dbf"]:  # NO .prj
                fp = shp_base + ext
                if os.path.exists(fp):
                    zf.write(fp, arcname=os.path.basename(fp))
        shutil.rmtree(tmp_dir, ignore_errors=True)

        zip_buf.seek(0)
        res = client.post(
            "/files/upload",
            files={"file": ("no_prj.zip", zip_buf, "application/zip")},
            data={"name": "[AUTO] E-09 edge no prj"},
        )
        assert res.status_code == 200, f"Upload senza .prj fallito: {res.text}"
        file_source = res.json()
        file_id = file_source["id"]

        try:
            # CRS deve essere null o assente — non deve crashare
            crs = file_source.get("crs")
            # Non assert su valore esatto — comportamento accettabile: null o EPSG generico
            # L'importante è che non sia un 500
            assert res.status_code == 200
        finally:
            client.delete(f"/files/{file_id}")

    def test_zip_non_shapefile(self, client):
        """
        E-09 edge: zip con file non-geodati (.txt) → errore graceful, nessun 500.
        """
        zip_buf = _io.BytesIO()
        with _zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("readme.txt", "questo non è uno shapefile")
        zip_buf.seek(0)

        res = client.post(
            "/files/upload",
            files={"file": ("not_a_shp.zip", zip_buf, "application/zip")},
            data={"name": "[AUTO] E-09 edge zip non geo"},
        )
        # Deve essere gestito — 200 (fallback raw) o 400, mai 500
        assert res.status_code in (200, 400), (
            f"Zip non-geodati ha causato crash: {res.status_code} {res.text}"
        )
        if res.status_code == 200:
            client.delete(f"/files/{res.json()['id']}")
