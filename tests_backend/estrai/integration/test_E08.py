"""
E-08 — Upload GeoPackage layer multipli
=========================================
Verifica:
  1. Upload .gpkg con 3 layer geometrici
  2. GET /files/{id}/layers → tutti e 3 i layer visibili
  3. Preview di ciascun layer → righe corrette, geometry presente
  4. Nessun crash su layer switching

Flusso API:
  POST /api/files/upload          → file_id (upload con layer=None = primo layer)
  GET  /api/files/{id}/layers     → lista layer ["comuni", "strade", "poi"]
  Per ogni layer:
    POST /api/files/upload (stesso file, layer=<name>) → file_id specifico
    GET  /api/files/{id}/preview  → righe + colonne

Prerequisiti:
  - Backend FastAPI attivo su localhost:8000
  - geopandas installato (per creare il file .gpkg di test)
"""

import io
import os
import tempfile
import pytest
import geopandas as gpd
from shapely.geometry import Point, LineString, Polygon

pytestmark = [pytest.mark.estrai]

# ─── Fixture: crea file .gpkg con 3 layer ─────────────────────────────────────

LAYERS = {
    "comuni": {
        "geometry_type": "Point",
        "rows": [
            {"nome": "Roma",   "pop": 2873000, "geometry": Point(12.4964, 41.9028)},
            {"nome": "Milano", "pop": 1352000, "geometry": Point(9.1900,  45.4642)},
            {"nome": "Napoli", "pop": 962000,  "geometry": Point(14.2681, 40.8518)},
        ],
    },
    "strade": {
        "geometry_type": "LineString",
        "rows": [
            {"nome": "Via Roma",   "km": 2.5, "geometry": LineString([(12.49, 41.90), (12.51, 41.91)])},
            {"nome": "Corso Italia", "km": 1.8, "geometry": LineString([(9.18, 45.46), (9.20, 45.47)])},
        ],
    },
    "poi": {
        "geometry_type": "Polygon",
        "rows": [
            {"nome": "Parco A", "area_ha": 12.0,
             "geometry": Polygon([(12.49, 41.90), (12.50, 41.90), (12.50, 41.91), (12.49, 41.91)])},
            {"nome": "Parco B", "area_ha": 8.5,
             "geometry": Polygon([(9.18, 45.46), (9.19, 45.46), (9.19, 45.47), (9.18, 45.47)])},
            {"nome": "Parco C", "area_ha": 5.2,
             "geometry": Polygon([(14.26, 40.85), (14.27, 40.85), (14.27, 40.86), (14.26, 40.86)])},
        ],
    },
}


@pytest.fixture(scope="module")
def gpkg_file():
    """Crea un file .gpkg temporaneo con 3 layer, lo elimina al teardown."""
    tmp = tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False)
    tmp.close()
    path = tmp.name

    for layer_name, layer_data in LAYERS.items():
        gdf = gpd.GeoDataFrame(layer_data["rows"], crs="EPSG:4326")
        gdf.to_file(path, layer=layer_name, driver="GPKG")

    yield path

    os.unlink(path)


# ─── Test ─────────────────────────────────────────────────────────────────────

class TestE08:

    def test_upload_gpkg(self, client, gpkg_file):
        """E-08a: upload del .gpkg va a buon fine."""
        with open(gpkg_file, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e08.gpkg", f, "application/octet-stream")},
                data={"name": "[AUTO] E-08 upload base"},
            )
        assert res.status_code == 200, f"Upload fallito: {res.text}"
        file_id = res.json().get("id")
        assert file_id, f"id mancante: {res.json()}"

        # Cleanup
        client.delete(f"/files/{file_id}")

    def test_lista_layer(self, client, gpkg_file):
        """E-08b: GET /layers restituisce tutti e 3 i layer del .gpkg."""
        with open(gpkg_file, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e08.gpkg", f, "application/octet-stream")},
                data={"name": "[AUTO] E-08 lista layer"},
            )
        assert res.status_code == 200
        file_id = res.json()["id"]

        try:
            res_layers = client.get(f"/files/{file_id}/layers")
            assert res_layers.status_code == 200, f"GET layers fallito: {res_layers.text}"

            layers_data = res_layers.json()
            layers = layers_data.get("layers", [])

            assert len(layers) == 3, f"Attesi 3 layer, trovati {len(layers)}: {layers}"
            for expected in ["comuni", "strade", "poi"]:
                assert expected in layers, f"Layer '{expected}' mancante: {layers}"

        finally:
            client.delete(f"/files/{file_id}")

    @pytest.mark.parametrize("layer_name,expected_rows,expected_col", [
        ("comuni", 3, "nome"),
        ("strade", 2, "nome"),
        ("poi",    3, "nome"),
    ])
    def test_preview_per_layer(self, client, gpkg_file, layer_name, expected_rows, expected_col):
        """
        E-08c: preview di ciascun layer restituisce righe corrette
        con colonna geometry presente.
        """
        with open(gpkg_file, "rb") as f:
            res = client.post(
                "/files/upload",
                files={"file": ("test_e08.gpkg", f, "application/octet-stream")},
                data={"name": f"[AUTO] E-08 preview {layer_name}", "layer": layer_name},
            )
        assert res.status_code == 200, f"Upload layer '{layer_name}' fallito: {res.text}"
        file_id = res.json()["id"]

        try:
            res_preview = client.get(f"/files/{file_id}/preview", params={"limit": 50})
            assert res_preview.status_code == 200, (
                f"Preview layer '{layer_name}' fallita: {res_preview.text}"
            )

            data = res_preview.json()

            # Righe corrette
            rows = data.get("rows", data.get("data", []))
            assert len(rows) == expected_rows, (
                f"Layer '{layer_name}': attese {expected_rows} righe, trovate {len(rows)}"
            )

            # Colonna attesa presente
            columns = [c["name"] if isinstance(c, dict) else c
                       for c in data.get("columns", [])]
            assert expected_col in columns, (
                f"Layer '{layer_name}': colonna '{expected_col}' mancante. Trovate: {columns}"
            )

            # Colonna geometry presente
            geom_cols = [c for c in columns if "geom" in c.lower() or "geometry" in c.lower()]
            assert geom_cols, (
                f"Layer '{layer_name}': nessuna colonna geometry trovata. Trovate: {columns}"
            )

        finally:
            client.delete(f"/files/{file_id}")
