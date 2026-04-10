"""
E-16 — CRS da .prj letto correttamente (EPSG:25832)
=====================================================
Verifica che il CRS venga letto dal file .prj di uno shapefile
con proiezione EPSG:25832 (ETRS89 / UTM zone 32N).

EPSG:25832 vs EPSG:32632:
  - 32632 = WGS84 / UTM zone 32N
  - 25832 = ETRS89 / UTM zone 32N  ← questo test
  Entrambi hanno coordinate simili (X ~ 300000–900000 per l'Italia)
  ma datum diverso. Il backend deve leggere il .prj correttamente.

Verifica:
  1. Upload .zip con shapefile + .prj EPSG:25832
  2. FileSource.crs contiene "25832"
  3. Coordinate nel preview restano in ETRS89/UTM (X > 1000)
  4. CRS non viene "normalizzato" a 4326 automaticamente

Prerequisiti:
  - Backend FastAPI attivo su localhost:8000
  - geopandas installato
"""

import os
import re
import zipfile
import tempfile
import shutil
import pytest
import geopandas as gpd
from shapely.geometry import Point

pytestmark = [pytest.mark.estrai]


@pytest.fixture(scope="module")
def shapefile_25832_zip():
    """
    Crea shapefile con CRS EPSG:25832 (ETRS89/UTM 32N),
    lo zippa con .prj incluso.
    """
    # Coordinate ETRS89/UTM 32N per 3 città italiane
    # (praticamente identiche a UTM 32N WGS84 per scopi pratici)
    rows = [
        {"nome": "Torino",  "codice": "TO", "geometry": Point(397000, 4992000)},
        {"nome": "Milano",  "codice": "MI", "geometry": Point(514000, 5034000)},
        {"nome": "Bologna", "codice": "BO", "geometry": Point(685000, 4930000)},
    ]
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:25832")

    tmp_dir = tempfile.mkdtemp()
    shp_base = os.path.join(tmp_dir, "test_e16_etrs89")
    gdf.to_file(shp_base + ".shp", driver="ESRI Shapefile")

    zip_path = os.path.join(tmp_dir, "test_e16_etrs89.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
            fp = shp_base + ext
            if os.path.exists(fp):
                zf.write(fp, arcname=os.path.basename(fp))

    yield zip_path
    shutil.rmtree(tmp_dir, ignore_errors=True)


class TestE16:

    def test_crs_25832_da_prj(self, client, shapefile_25832_zip):
        """
        E-16a: FileSource.crs contiene "25832" — letto dal .prj.
        Non deve essere 4326 o None.
        """
        with open(shapefile_25832_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e16_etrs89.zip", f, "application/zip")},
                data={"name": "[AUTO] E-16 CRS 25832"},
            )
        assert res.status_code == 200, f"Upload fallito: {res.text}"
        file_source = res.json()
        file_id = file_source["id"]

        try:
            crs = file_source.get("crs")
            assert crs is not None, "CRS null — .prj non letto"
            assert "25832" in str(crs), (
                f"CRS atteso EPSG:25832, trovato: '{crs}'"
            )
            # Non deve essere 4326
            assert "4326" not in str(crs), (
                f"CRS riproiettato a 4326 automaticamente: '{crs}'"
            )
        finally:
            client.delete(f"/files/{file_id}")

    def test_coordinate_etrs89_non_riproiettate(self, client, shapefile_25832_zip):
        """
        E-16b: le coordinate nel preview restano in ETRS89/UTM 32N (X > 1000).
        """
        with open(shapefile_25832_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e16_etrs89.zip", f, "application/zip")},
                data={"name": "[AUTO] E-16 coordinate ETRS89"},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview", params={"limit": 10})
            assert res_preview.status_code == 200, f"Preview fallita: {res_preview.text}"

            data = res_preview.json()
            rows = data.get("rows", data.get("data", []))
            columns = [c["name"] if isinstance(c, dict) else c
                       for c in data.get("columns", [])]

            assert len(rows) == 3, f"Attese 3 righe, trovate {len(rows)}"

            geom_cols = [c for c in columns if "geom" in c.lower() or "geometry" in c.lower()]
            assert geom_cols, f"Nessuna colonna geometry: {columns}"

            geom_col = geom_cols[0]
            for row in rows:
                geom_val = str(row.get(geom_col, ""))
                assert geom_val and geom_val != "None", f"Geometria null: {row}"

                coords = re.findall(r"[-\d.]+", geom_val)
                if coords:
                    x = float(coords[0])
                    assert x > 1000, (
                        f"Coordinate sembrano WGS84: x={x}. "
                        f"Atteso ETRS89/UTM (x ~ 400000–700000). WKT: {geom_val}"
                    )

        finally:
            client.delete(f"/files/{file_id}")

    def test_attributi_presenti(self, client, shapefile_25832_zip):
        """E-16c: attributi nome e codice presenti con valori corretti."""
        with open(shapefile_25832_zip, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e16_etrs89.zip", f, "application/zip")},
                data={"name": "[AUTO] E-16 attributi"},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview", params={"limit": 10})
            assert res_preview.status_code == 200

            data = res_preview.json()
            rows = data.get("rows", data.get("data", []))
            columns = [c["name"] if isinstance(c, dict) else c
                       for c in data.get("columns", [])]

            assert "nome" in columns, f"Colonna 'nome' mancante: {columns}"
            assert "codice" in columns, f"Colonna 'codice' mancante: {columns}"

            nomi = {r.get("nome") for r in rows}
            assert "Torino" in nomi
            assert "Milano" in nomi
            assert "Bologna" in nomi

        finally:
            client.delete(f"/files/{file_id}")
