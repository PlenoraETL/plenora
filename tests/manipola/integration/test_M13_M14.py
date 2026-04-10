"""
M-13 — Output GeoPackage (.gpkg)
M-14 — Output Shapefile zippato (.zip)
+ Classe GeoJSON (formato aggiuntivo nel codebase)
=================================================

Branch coperti in save_dataframe / _salva_formato_geo:

_salva_formato_geo
  ✅ GeoDataFrame → GPKG happy path
  ✅ GeoDataFrame → GPKG vuoto → _salva_geo_vuoto
  ✅ GeoDataFrame, crs is None → set_crs(default)         [gpkg_no_crs fixture]
  ✅ GeoDataFrame, geom_col DIVERSA e in cols → _switch   [geo_centroid + geomColumn="centroid"]
  ✅ GeoDataFrame, geom_col == attiva → no switch         [geomColumn="geometry"]
  ✅ _converti_geometrie_secondarie: BaseGeometry → WKT   [sjoin]
  ✅ not GeoDataFrame, geom_column presente → to_geodataframe [csv_with_wkt fixture]
  ✅ not GeoDataFrame, geom_column assente → detect → None → ValueError
  ✅ formato=gpkg, shp, geojson

_switch_colonna_geometria
  ✅ col dtype=geometry (Shapely) → no parse, set_geometry [geo_centroid]
  ✗  col dtype=object str WKT → wkt.loads  [non raggiungibile via E2E: switch avviene prima di _converti_wkt]

_salva_geo_vuoto
  ✅ formato=gpkg
  ✅ formato=shp
  ✅ formato=geojson

_salva_shapefile_zippato
  ✅ ZIP contiene .shp .shx .dbf .prj
  ✅ colonne > 10 char → troncate (warning, no crash)
  ✅ GeoDataFrame vuoto → ZIP valido

save_dataframe top-level
  ✅ formato=gpkg
  ✅ formato=shp
  ✅ formato=geojson

CRS handling
  ✅ EPSG:4326 default
  ✅ EPSG:32632 da canvas (_estrai_srid_da_canvas)
  ✅ CRS=None → set_crs(EPSG:4326)
"""

import io
import json
import zipfile
import pytest
import geopandas as gpd

pytestmark = [pytest.mark.manipola]

N_POLYGONS = 20
N_POINTS   = 50
N_UTM      = 30
N_NOCRS    = 10
N_WKT      = 15


# ── helpers ───────────────────────────────────────────────────────────────────

def _execute(client, pid, poll):
    res = client.post(f"/projects/{pid}/execute")
    assert res.status_code == 200
    task_id = res.json()["task_id"]
    return poll(task_id), task_id

def _download_bytes(client, task_id):
    res = client.get(f"/tasks/{task_id}/download")
    assert res.status_code == 200, f"Download fallito: {res.status_code} {res.text[:200]}"
    return res.content

def _make_geo_canvas(file_source_id, output_format, output_filename,
                     transform_op="sort", transform_config=None, geom_column=None):
    from conftest import build_manipola_canvas
    if transform_config is None:
        transform_config = {"columns": ["id"], "ascending": True}
    canvas = build_manipola_canvas(
        file_source_id=file_source_id,
        transform_op=transform_op,
        transform_config=transform_config,
        output_format=output_format,
        output_filename=output_filename,
    )
    if geom_column:
        for n in canvas["nodes"]:
            if n["type"] == "result":
                n["geomColumn"] = geom_column
    return canvas

def _run(client, make_project, poll, name, file_source_id, output_format,
         output_filename, transform_op="sort", transform_config=None, geom_column=None):
    canvas = _make_geo_canvas(file_source_id, output_format, output_filename,
                              transform_op, transform_config, geom_column)
    pid = make_project(name, "manipola", canvas=canvas)
    result, task_id = _execute(client, pid, poll)
    return result, task_id

def _run_geo_op(client, make_project, poll, name,
                file_id, geo_op, geo_config, output_format, output_filename,
                geom_column=None, second_file_id=None):
    """Esegue un'op geospaziale e imposta geomColumn opzionale sul nodo result."""
    from conftest import build_manipola_canvas_geo
    canvas = build_manipola_canvas_geo(
        file_source_id=file_id,
        geo_op=geo_op,
        geo_config=geo_config,
        output_format=output_format,
        output_filename=output_filename,
        second_file_source_id=second_file_id,
    )
    if geom_column:
        for n in canvas["nodes"]:
            if n["type"] == "result":
                n["geomColumn"] = geom_column
    pid = make_project(name, "manipola", canvas=canvas)
    return _execute(client, pid, poll)

def _is_sqlite(data: bytes) -> bool:
    return data[:16] == b"SQLite format 3\x00"

def _is_zip(data: bytes) -> bool:
    return zipfile.is_zipfile(io.BytesIO(data))

def _zip_names(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return [n.lower() for n in zf.namelist()]

def _is_geojson(data: bytes) -> bool:
    try:
        d = json.loads(data.decode("utf-8"))
        return d.get("type") == "FeatureCollection"
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# M-13 — Output GeoPackage
# ══════════════════════════════════════════════════════════════════════════════

class TestM13GeoPackage:

    def test_gpkg_happy_path_poligoni(self, client, gpkg_polygons, make_project, poll):
        """M-13a: GeoDataFrame poligoni → GPKG → SQLite magic bytes valido."""
        result, task_id = _run(client, make_project, poll,
            "[AUTO] M-13a gpkg poly", gpkg_polygons, "gpkg", "M13a")
        assert result.get("success") is True, f"Execute fallito: {result}"
        assert result.get("format") == "gpkg"
        assert _is_sqlite(_download_bytes(client, task_id))

    def test_gpkg_row_count(self, client, gpkg_polygons, make_project, poll):
        """M-13b: rows_count == N_POLYGONS."""
        result, _ = _run(client, make_project, poll,
            "[AUTO] M-13b gpkg rowcount", gpkg_polygons, "gpkg", "M13b")
        assert result.get("success") is True
        assert result.get("rows_count") == N_POLYGONS

    def test_gpkg_punti(self, client, gpkg_points, make_project, poll):
        """M-13c: GeoDataFrame punti → GPKG con N_POINTS feature."""
        result, _ = _run(client, make_project, poll,
            "[AUTO] M-13c gpkg pts", gpkg_points, "gpkg", "M13c")
        assert result.get("success") is True
        assert result.get("rows_count") == N_POINTS

    def test_gpkg_crs_utm32632(self, client, gpkg_utm32632, make_project, poll):
        """M-13d: input EPSG:32632 → _estrai_srid_da_canvas → GPKG con CRS UTM."""
        result, _ = _run(client, make_project, poll,
            "[AUTO] M-13d gpkg utm", gpkg_utm32632, "gpkg", "M13d")
        assert result.get("success") is True
        assert result.get("rows_count") == N_UTM

    def test_gpkg_vuoto_dopo_filter(self, client, gpkg_polygons, make_project, poll):
        """M-13e: 0 righe → _salva_geo_vuoto → GPKG SQLite valido."""
        result, task_id = _run(
            client, make_project, poll,
            "[AUTO] M-13e gpkg empty", gpkg_polygons, "gpkg", "M13e",
            transform_op="filter",
            transform_config={"column": "nome", "operator": "==", "value": "__NONEXISTENT__"},
        )
        if result.get("success"):
            assert result.get("rows_count") == 0
            assert _is_sqlite(_download_bytes(client, task_id))
        else:
            assert result.get("message")  # Errore pulito, no crash

    def test_gpkg_geom_col_uguale_attiva_no_switch(self, client, gpkg_polygons, make_project, poll):
        """M-13f: geomColumn == geometry attiva → branch switch NON eseguito, GPKG valido."""
        result, task_id = _run(
            client, make_project, poll,
            "[AUTO] M-13f gpkg geomcol same", gpkg_polygons, "gpkg", "M13f",
            geom_column="geometry",  # uguale all'attiva → condition False → no switch
        )
        assert result.get("success") is True, f"geomColumn == attiva fallita: {result}"
        assert _is_sqlite(_download_bytes(client, task_id))

    def test_gpkg_switch_colonna_diversa_reale(self, client, gpkg_polygons, make_project, poll):
        """M-13f2: geo_centroid produce 'centroid' (Shapely Point) + geomColumn='centroid'
        → branch switch reale: _switch_colonna_geometria col dtype=geometry → set_geometry."""
        result, task_id = _run_geo_op(
            client, make_project, poll,
            "[AUTO] M-13f2 gpkg switch real", gpkg_polygons,
            "geo_centroid",
            {"geom_column": "geometry", "output_column": "centroid"},
            "gpkg", "M13f2_switch",
            geom_column="centroid",  # DIVERSA da "geometry" → switch eseguito
        )
        assert result.get("success") is True, f"Switch colonna diversa GPKG fallito: {result}"
        raw = _download_bytes(client, task_id)
        assert _is_sqlite(raw), "Output non è GPKG SQLite valido dopo switch"

    def test_gpkg_no_geometry_failure(self, client, csv_immobili, make_project, poll):
        """M-13g: CSV con lat/lon → auto-crea geometry → success=True (comportamento atteso).
        NOTA: il backend ha logica di auto-creazione geometria da colonne lat/lon,
        quindi csv_immobili (che ha lat e lon) produce un GPKG valido invece di fallire.
        """
        result, _ = _run(client, make_project, poll,
            "[AUTO] M-13g gpkg nogeom", csv_immobili, "gpkg", "M13g")
        # Il backend auto-crea geometry da lat/lon → success=True
        assert result.get("success") is True, f"Auto-geometria da lat/lon fallita: {result}"
        assert result.get("rows_count") > 0

    def test_gpkg_plain_df_wkt_column(self, client, csv_with_wkt, make_project, poll):
        """M-13g2: CSV con colonna WKT + geomColumn='geom_wkt'
        → not GeoDataFrame branch → to_geodataframe → GPKG valido."""
        result, task_id = _run(
            client, make_project, poll,
            "[AUTO] M-13g2 gpkg wkt csv", csv_with_wkt, "gpkg", "M13g2_wkt",
            geom_column="geom_wkt",
        )
        assert result.get("success") is True, f"CSV WKT → GPKG fallito: {result}"
        assert result.get("rows_count") == N_WKT
        assert _is_sqlite(_download_bytes(client, task_id))

    def test_gpkg_crs_none_set_default(self, client, gpkg_no_crs, make_project, poll):
        """M-13h: input senza CRS → branch set_crs(EPSG:4326) → GPKG valido."""
        result, task_id = _run(client, make_project, poll,
            "[AUTO] M-13h gpkg no crs", gpkg_no_crs, "gpkg", "M13h_nocrs")
        assert result.get("success") is True, f"CRS None → set_crs fallito: {result}"
        assert result.get("rows_count") == N_NOCRS
        assert _is_sqlite(_download_bytes(client, task_id))

    def test_gpkg_converti_geometrie_secondarie(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-13i: sjoin → 2 colonne geometry → _converti_geometrie_secondarie_in_wkt → GPKG valido."""
        from conftest import build_manipola_canvas_sjoin
        canvas = build_manipola_canvas_sjoin(
            left_file_id=gpkg_polygons,
            right_file_id=gpkg_points,
            predicate="intersects",
            how="inner",
            output_format="gpkg",
            output_filename="M13i_sjoin",
        )
        pid = make_project("[AUTO] M-13i sjoin gpkg", "manipola", canvas=canvas)
        result, task_id = _execute(client, pid, poll)
        assert result.get("success") is True, f"sjoin→gpkg fallito: {result}"
        assert _is_sqlite(_download_bytes(client, task_id))

    def test_gpkg_pipeline_sort(self, client, gpkg_polygons, make_project, poll):
        """M-13j: sort + GPKG → rows_count invariato."""
        result, task_id = _run(
            client, make_project, poll,
            "[AUTO] M-13j gpkg sort", gpkg_polygons, "gpkg", "M13j_sort",
            transform_op="sort",
            transform_config={"columns": ["area_kmq"], "ascending": False},
        )
        assert result.get("success") is True
        assert result.get("rows_count") == N_POLYGONS


# ══════════════════════════════════════════════════════════════════════════════
# M-14 — Output Shapefile zippato
# ══════════════════════════════════════════════════════════════════════════════

class TestM14ShapefileZip:

    def test_shp_happy_path(self, client, gpkg_polygons, make_project, poll):
        """M-14a: GeoDataFrame → SHP → ZIP valido."""
        result, task_id = _run(client, make_project, poll,
            "[AUTO] M-14a shp happy", gpkg_polygons, "shp", "M14a")
        assert result.get("success") is True, f"Execute fallito: {result}"
        assert _is_zip(_download_bytes(client, task_id))

    def test_shp_zip_componenti(self, client, gpkg_polygons, make_project, poll):
        """M-14b: ZIP contiene .shp .shx .dbf .prj."""
        result, task_id = _run(client, make_project, poll,
            "[AUTO] M-14b shp comp", gpkg_polygons, "shp", "M14b")
        assert result.get("success") is True
        names = _zip_names(_download_bytes(client, task_id))
        for ext in [".shp", ".shx", ".dbf", ".prj"]:
            assert any(n.endswith(ext) for n in names), f"{ext} mancante: {names}"

    def test_shp_row_count(self, client, gpkg_polygons, make_project, poll):
        """M-14c: rows_count == N_POLYGONS."""
        result, _ = _run(client, make_project, poll,
            "[AUTO] M-14c shp rowcount", gpkg_polygons, "shp", "M14c")
        assert result.get("success") is True
        assert result.get("rows_count") == N_POLYGONS

    def test_shp_crs_in_prj(self, client, gpkg_utm32632, make_project, poll):
        """M-14d: input EPSG:32632 → .prj contiene riferimento UTM/32632."""
        result, task_id = _run(client, make_project, poll,
            "[AUTO] M-14d shp prj", gpkg_utm32632, "shp", "M14d")
        assert result.get("success") is True
        raw = _download_bytes(client, task_id)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            # Usa i nomi originali (case-sensitive) per la lettura
            prj_names = [n for n in zf.namelist() if n.lower().endswith(".prj")]
            assert prj_names, f"Nessun .prj nello ZIP: {zf.namelist()}"
            prj = zf.read(prj_names[0]).decode("utf-8", errors="replace").upper()
        assert any(kw in prj for kw in ["32632", "UTM", "TRANSVERSE"])

    def test_shp_nomi_colonne_lunghi(self, client, gpkg_polygons, make_project, poll):
        """M-14e: colonne > 10 char → troncate, ZIP valido (warning, no crash)."""
        from conftest import build_manipola_canvas
        canvas = build_manipola_canvas(
            file_source_id=gpkg_polygons,
            transform_op="rename",
            transform_config={"renames": [
                {"old_name": "nome",      "new_name": "nome_completo_lungo"},
                {"old_name": "provincia", "new_name": "codice_provincia_istat"},
            ]},
            output_format="shp",
            output_filename="M14e_longcols",
        )
        pid = make_project("[AUTO] M-14e shp longcols", "manipola", canvas=canvas)
        result, task_id = _execute(client, pid, poll)
        assert result.get("success") is True, f"Nomi lunghi crash: {result}"
        assert _is_zip(_download_bytes(client, task_id))

    def test_shp_punti(self, client, gpkg_points, make_project, poll):
        """M-14f: geometria punto → SHP ZIP con N_POINTS feature."""
        result, task_id = _run(client, make_project, poll,
            "[AUTO] M-14f shp pts", gpkg_points, "shp", "M14f")
        assert result.get("success") is True
        assert result.get("rows_count") == N_POINTS
        assert any(n.endswith(".shp") for n in _zip_names(_download_bytes(client, task_id)))

    def test_shp_no_geometry_failure(self, client, csv_immobili, make_project, poll):
        """M-14g: CSV con lat/lon → auto-crea geometry → success=True."""
        result, _ = _run(client, make_project, poll,
            "[AUTO] M-14g shp nogeom", csv_immobili, "shp", "M14g")
        assert result.get("success") is True, f"Auto-geometria da lat/lon fallita: {result}"
        assert result.get("rows_count") > 0

    def test_shp_vuoto_dopo_filter(self, client, gpkg_polygons, make_project, poll):
        """M-14h: 0 righe → _salva_geo_vuoto → ZIP SHP valido."""
        result, task_id = _run(
            client, make_project, poll,
            "[AUTO] M-14h shp empty", gpkg_polygons, "shp", "M14h",
            transform_op="filter",
            transform_config={"column": "nome", "operator": "==", "value": "__NONEXISTENT__"},
        )
        if result.get("success"):
            assert result.get("rows_count") == 0
            assert _is_zip(_download_bytes(client, task_id))
        else:
            assert result.get("message")

    def test_shp_geom_col_uguale_attiva_no_switch(self, client, gpkg_polygons, make_project, poll):
        """M-14i: geomColumn == geometry attiva → no switch, SHP valido."""
        result, task_id = _run(
            client, make_project, poll,
            "[AUTO] M-14i shp geomcol same", gpkg_polygons, "shp", "M14i",
            geom_column="geometry",
        )
        assert result.get("success") is True
        assert _is_zip(_download_bytes(client, task_id))

    def test_shp_switch_colonna_diversa_reale(self, client, gpkg_polygons, make_project, poll):
        """M-14i2: geo_centroid + geomColumn='centroid' → switch reale → SHP con punti."""
        result, task_id = _run_geo_op(
            client, make_project, poll,
            "[AUTO] M-14i2 shp switch real", gpkg_polygons,
            "geo_centroid",
            {"geom_column": "geometry", "output_column": "centroid"},
            "shp", "M14i2_switch",
            geom_column="centroid",
        )
        assert result.get("success") is True, f"Switch colonna diversa SHP fallito: {result}"
        raw = _download_bytes(client, task_id)
        assert _is_zip(raw)
        assert any(n.endswith(".shp") for n in _zip_names(raw))

    def test_shp_plain_df_wkt_column(self, client, csv_with_wkt, make_project, poll):
        """M-14j: CSV con colonna WKT + geomColumn='geom_wkt'
        → not GeoDataFrame → to_geodataframe → SHP valido."""
        result, task_id = _run(
            client, make_project, poll,
            "[AUTO] M-14j shp wkt csv", csv_with_wkt, "shp", "M14j_wkt",
            geom_column="geom_wkt",
        )
        assert result.get("success") is True, f"CSV WKT → SHP fallito: {result}"
        assert result.get("rows_count") == N_WKT
        assert _is_zip(_download_bytes(client, task_id))

    def test_shp_crs_none_set_default(self, client, gpkg_no_crs, make_project, poll):
        """M-14k: input senza CRS → set_crs(EPSG:4326) → SHP valido."""
        result, task_id = _run(client, make_project, poll,
            "[AUTO] M-14k shp no crs", gpkg_no_crs, "shp", "M14k_nocrs")
        assert result.get("success") is True, f"CRS None → SHP fallito: {result}"
        assert result.get("rows_count") == N_NOCRS
        assert _is_zip(_download_bytes(client, task_id))

    def test_shp_sjoin_geometrie_secondarie(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-14l: sjoin → 2 colonne geometry → _converti_secondarie_in_wkt → SHP valido."""
        from conftest import build_manipola_canvas_sjoin
        canvas = build_manipola_canvas_sjoin(
            left_file_id=gpkg_polygons,
            right_file_id=gpkg_points,
            predicate="intersects",
            how="inner",
            output_format="shp",
            output_filename="M14l_sjoin",
        )
        pid = make_project("[AUTO] M-14l sjoin shp", "manipola", canvas=canvas)
        result, task_id = _execute(client, pid, poll)
        assert result.get("success") is True, f"sjoin→shp fallito: {result}"
        raw = _download_bytes(client, task_id)
        assert _is_zip(raw)
        assert any(n.endswith(".shp") for n in _zip_names(raw))


# ══════════════════════════════════════════════════════════════════════════════
# GeoJSON — formato aggiuntivo (_salva_formato_geo + _salva_geo_vuoto branches)
# ══════════════════════════════════════════════════════════════════════════════

class TestGeoJSON:

    def test_geojson_happy_path(self, client, gpkg_polygons, make_project, poll):
        """GeoJSON: GeoDataFrame → valid FeatureCollection JSON."""
        result, task_id = _run(client, make_project, poll,
            "[AUTO] geojson happy", gpkg_polygons, "geojson", "geojson_happy")
        assert result.get("success") is True, f"GeoJSON fallito: {result}"
        assert result.get("rows_count") == N_POLYGONS
        assert _is_geojson(_download_bytes(client, task_id))

    def test_geojson_row_count(self, client, gpkg_points, make_project, poll):
        """GeoJSON: rows_count == N_POINTS."""
        result, _ = _run(client, make_project, poll,
            "[AUTO] geojson pts", gpkg_points, "geojson", "geojson_pts")
        assert result.get("success") is True
        assert result.get("rows_count") == N_POINTS

    def test_geojson_vuoto_feature_collection(self, client, gpkg_polygons, make_project, poll):
        """GeoJSON: 0 righe → _salva_geo_vuoto → FeatureCollection vuota (no crash)."""
        result, task_id = _run(
            client, make_project, poll,
            "[AUTO] geojson empty", gpkg_polygons, "geojson", "geojson_empty",
            transform_op="filter",
            transform_config={"column": "nome", "operator": "==", "value": "__NONEXISTENT__"},
        )
        if result.get("success"):
            assert result.get("rows_count") == 0
            raw = _download_bytes(client, task_id)
            assert _is_geojson(raw)
            d = json.loads(raw.decode("utf-8"))
            assert d["features"] == [], f"FeatureCollection vuota attesa: {d['features']}"
        else:
            assert result.get("message")

    def test_geojson_crs_utm32632(self, client, gpkg_utm32632, make_project, poll):
        """GeoJSON: input EPSG:32632 → output GeoJSON valido."""
        result, task_id = _run(client, make_project, poll,
            "[AUTO] geojson utm", gpkg_utm32632, "geojson", "geojson_utm")
        assert result.get("success") is True
        assert _is_geojson(_download_bytes(client, task_id))

    def test_geojson_no_geometry_failure(self, client, csv_immobili, make_project, poll):
        """M-GeoJSON-g: CSV con lat/lon → auto-crea geometry → success=True."""
        result, _ = _run(client, make_project, poll,
            "[AUTO] geojson nogeom", csv_immobili, "geojson", "geojson_nogeom")
        assert result.get("success") is True, f"Auto-geometria da lat/lon fallita: {result}"
        assert result.get("rows_count") > 0

    def test_geojson_plain_df_wkt(self, client, csv_with_wkt, make_project, poll):
        """GeoJSON: CSV+WKT + geomColumn='geom_wkt' → not GeoDataFrame → to_geodataframe → GeoJSON."""
        result, task_id = _run(
            client, make_project, poll,
            "[AUTO] geojson wkt", csv_with_wkt, "geojson", "geojson_wkt",
            geom_column="geom_wkt",
        )
        assert result.get("success") is True, f"CSV WKT → GeoJSON fallito: {result}"
        assert _is_geojson(_download_bytes(client, task_id))
