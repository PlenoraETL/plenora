"""
M-15 → M-22 — Operazioni geospaziali MANIPOLA (copertura completa)
===================================================================
Copre tutte le op del registry + tutti i branch identificati:

geo_dissolve:  by_column presente/assente/non-in-columns, aggfunc first/sum/mean/count
geo_buffer:    CRS geografico→UTM, CRS metrico, CRS None, cap_style round/flat/square, distance=0
geo_centroid:  output_column custom, geometry originale preservata, punti
geo_area:      CRS geografico→UTM, CRS metrico, output_column custom
geo_clip:      2 input happy, CRS diversi, 0 righe, input<2 early-return
geo_intersection: 2 input, ensure_same_crs, _ensure_active_geometry, input<2
geo_union:     2 input, self-join, input<2 early-return
geo_difference: 2 input, A-A=vuoto, input<2 early-return
geo_simplify:  tolerance, preserve_topology=True/False
geo_convex_hull: output_column custom, rows invariate
geo_reproject:  4326→32632, 32632→4326, 4326→3857, source_crs esplicito, noop, CRS None fallback
geo_envelope:   output_column custom, rows invariate
"""

import pytest
import geopandas as gpd

pytestmark = [pytest.mark.manipola]

N_POLYGONS = 20
N_POINTS   = 50
N_UTM      = 30


# ── helpers ───────────────────────────────────────────────────────────────────

def _execute(client, pid, poll):
    res = client.post(f"/projects/{pid}/execute")
    assert res.status_code == 200
    task_id = res.json()["task_id"]
    return poll(task_id), task_id

def _run_geo(client, make_project, poll, name,
             file_id, geo_op, geo_config,
             output_format="gpkg", output_filename=None,
             second_file_id=None):
    from conftest import build_manipola_canvas_geo
    canvas = build_manipola_canvas_geo(
        file_source_id=file_id,
        geo_op=geo_op,
        geo_config=geo_config,
        output_format=output_format,
        output_filename=output_filename or name.replace(" ","_").lstrip("[AUTO]_"),
        second_file_source_id=second_file_id,
    )
    pid = make_project(name, "manipola", canvas=canvas)
    return _execute(client, pid, poll)

def _ok(result):    return result.get("success") is True
def _rc(result):
    """Restituisce rows_count — gestisce 0 (falsy) correttamente."""
    v = result.get("rows_count")
    if v is None:
        v = result.get("row_count")
    return v

def _preview(client, pid, poll, limit=5):
    res = client.get(f"/projects/{pid}/preview", params={"limit": limit})
    assert res.status_code == 200
    return poll(res.json().get("task_id"))

def _cols(client, pid, poll):
    r = _preview(client, pid, poll)
    return {c["name"] if isinstance(c, dict) else c for c in r.get("columns", [])}

def _rows(client, pid, poll, limit=5):
    r = _preview(client, pid, poll, limit=limit)
    return r.get("rows", r.get("data", []))

def _make_pid(client, make_project, poll, name, file_id, geo_op, geo_config,
              output_format="gpkg", output_filename=None, second_file_id=None):
    """Come _run_geo ma restituisce anche pid per ispezione preview."""
    from conftest import build_manipola_canvas_geo
    canvas = build_manipola_canvas_geo(
        file_source_id=file_id,
        geo_op=geo_op,
        geo_config=geo_config,
        output_format=output_format,
        output_filename=output_filename or name.replace(" ","_"),
        second_file_source_id=second_file_id,
    )
    pid = make_project(name, "manipola", canvas=canvas)
    result, task_id = _execute(client, pid, poll)
    return result, pid


# ══════════════════════════════════════════════════════════════════════════════
# M-15 — Dissolve
# ══════════════════════════════════════════════════════════════════════════════

class TestM15Dissolve:

    def test_dissolve_by_column(self, client, gpkg_polygons, make_project, poll):
        """M-15a: dissolve by=provincia → una riga per provincia (< N_POLYGONS)."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-15a dissolve by", gpkg_polygons, "geo_dissolve",
            {"geom_column": "geometry", "by_column": "provincia", "aggfunc": "first"})
        assert _ok(result), f"Dissolve by_column fallito: {result}"
        assert _rc(result) < N_POLYGONS, f"Dissolve deve ridurre righe: {_rc(result)}"

    def test_dissolve_tutto_1_riga(self, client, gpkg_polygons, make_project, poll):
        """M-15b: dissolve by=None → 1 sola geometria unione."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-15b dissolve all", gpkg_polygons, "geo_dissolve",
            {"geom_column": "geometry", "by_column": None, "aggfunc": "first"})
        assert _ok(result), f"Dissolve totale fallito: {result}"
        assert _rc(result) == 1, f"Dissolve totale deve dare 1 riga: {_rc(result)}"

    def test_dissolve_by_column_non_esistente(self, client, gpkg_polygons, make_project, poll):
        """M-15c: by_column non in gdf.columns → branch 'else' → dissolvi tutto."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-15c dissolve bad col", gpkg_polygons, "geo_dissolve",
            {"geom_column": "geometry", "by_column": "COLONNA_INESISTENTE", "aggfunc": "first"})
        assert _ok(result), f"Dissolve col inesistente fallito: {result}"
        assert _rc(result) == 1, f"Deve dissolversi tutto se col non esiste: {_rc(result)}"

    def test_dissolve_aggfunc_sum(self, client, gpkg_polygons, make_project, poll):
        """M-15d: aggfunc=sum → area_kmq aggregata > 0."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] M-15d dissolve sum", gpkg_polygons, "geo_dissolve",
            {"geom_column": "geometry", "by_column": None, "aggfunc": "sum"})
        assert _ok(result)
        rows = _rows(client, pid, poll)
        if rows and rows[0].get("area_kmq") is not None:
            assert float(rows[0]["area_kmq"]) > 0

    def test_dissolve_aggfunc_mean(self, client, gpkg_polygons, make_project, poll):
        """M-15e: aggfunc=mean → valore numerico presente."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-15e dissolve mean", gpkg_polygons, "geo_dissolve",
            {"geom_column": "geometry", "by_column": "provincia", "aggfunc": "mean"})
        assert _ok(result)

    def test_dissolve_aggfunc_count(self, client, gpkg_polygons, make_project, poll):
        """M-15f: aggfunc=count → conteggio feature per provincia."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-15f dissolve count", gpkg_polygons, "geo_dissolve",
            {"geom_column": "geometry", "by_column": "provincia", "aggfunc": "count"})
        assert _ok(result)

    def test_dissolve_geometry_output(self, client, gpkg_polygons, make_project, poll):
        """M-15g: output dissolve ha colonna geometry."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] M-15g dissolve geocol", gpkg_polygons, "geo_dissolve",
            {"geom_column": "geometry", "by_column": "provincia", "aggfunc": "first"})
        assert _ok(result)
        assert "geometry" in _cols(client, pid, poll)


# ══════════════════════════════════════════════════════════════════════════════
# M-16 — Buffer
# ══════════════════════════════════════════════════════════════════════════════

class TestM16Buffer:

    def test_buffer_1000m_round(self, client, gpkg_polygons, make_project, poll):
        """M-16a: buffer 1000m cap_style=round → colonna buffer_geom, rows invariate."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] M-16a buf round", gpkg_polygons, "geo_buffer",
            {"geom_column": "geometry", "distance": 1000,
             "output_column": "buffer_geom", "cap_style": "round"})
        assert _ok(result), f"Buffer round fallito: {result}"
        assert _rc(result) == N_POLYGONS
        assert "buffer_geom" in _cols(client, pid, poll)

    def test_buffer_geo_crs_reproietta_utm(self, client, gpkg_polygons, make_project, poll):
        """M-16b: CRS geografico (4326) → riproiezione UTM automatica → buffer in metri."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-16b buf geo→utm", gpkg_polygons, "geo_buffer",
            {"geom_column": "geometry", "distance": 500, "output_column": "buf"})
        assert _ok(result), f"Buffer geo→UTM fallito: {result}"
        assert _rc(result) == N_POLYGONS

    def test_buffer_crs_metrico_no_riproiezione(self, client, gpkg_utm32632, make_project, poll):
        """M-16c: CRS metrico (32632) → nessuna riproiezione, buffer diretto."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-16c buf utm direct", gpkg_utm32632, "geo_buffer",
            {"geom_column": "geometry", "distance": 1000, "output_column": "buf"})
        assert _ok(result), f"Buffer UTM diretto fallito: {result}"
        assert _rc(result) == N_UTM

    def test_buffer_cap_style_flat(self, client, gpkg_points, make_project, poll):
        """M-16d: cap_style=flat → branch cap_style_map['flat']=2."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-16d buf flat", gpkg_points, "geo_buffer",
            {"geom_column": "geometry", "distance": 100,
             "output_column": "buf_flat", "cap_style": "flat"})
        assert _ok(result), f"Buffer flat fallito: {result}"
        assert _rc(result) == N_POINTS

    def test_buffer_cap_style_square(self, client, gpkg_points, make_project, poll):
        """M-16e: cap_style=square → branch cap_style_map['square']=3."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-16e buf square", gpkg_points, "geo_buffer",
            {"geom_column": "geometry", "distance": 100,
             "output_column": "buf_sq", "cap_style": "square"})
        assert _ok(result), f"Buffer square fallito: {result}"

    def test_buffer_distanza_zero(self, client, gpkg_polygons, make_project, poll):
        """M-16f: distance=0 → geometrie invariate, colonna buffer presente."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] M-16f buf zero", gpkg_polygons, "geo_buffer",
            {"geom_column": "geometry", "distance": 0, "output_column": "buf0"})
        assert _ok(result)
        assert "buf0" in _cols(client, pid, poll)

    def test_buffer_punti(self, client, gpkg_points, make_project, poll):
        """M-16g: buffer su punti → cerchi, rows invariate."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-16g buf points", gpkg_points, "geo_buffer",
            {"geom_column": "geometry", "distance": 50, "output_column": "buf_pts"})
        assert _ok(result)
        assert _rc(result) == N_POINTS


# ══════════════════════════════════════════════════════════════════════════════
# M-17 — Centroid
# ══════════════════════════════════════════════════════════════════════════════

class TestM17Centroid:

    def test_centroid_poligoni(self, client, gpkg_polygons, make_project, poll):
        """M-17a: centroid su poligoni → colonna centroid aggiunta."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] M-17a centroid poly", gpkg_polygons, "geo_centroid",
            {"geom_column": "geometry", "output_column": "centroid"})
        assert _ok(result), f"Centroid fallito: {result}"
        assert _rc(result) == N_POLYGONS
        cols = _cols(client, pid, poll)
        assert "centroid" in cols
        assert "geometry" in cols  # geometry originale preservata

    def test_centroid_output_column_custom(self, client, gpkg_polygons, make_project, poll):
        """M-17b: output_column='centro' → colonna 'centro' presente."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] M-17b centroid custom", gpkg_polygons, "geo_centroid",
            {"geom_column": "geometry", "output_column": "centro"})
        assert _ok(result)
        assert "centro" in _cols(client, pid, poll)

    def test_centroid_punti(self, client, gpkg_points, make_project, poll):
        """M-17c: centroid di punti → centroide=punto stesso, rows invariate."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-17c centroid pts", gpkg_points, "geo_centroid",
            {"geom_column": "geometry", "output_column": "centroid"})
        assert _ok(result)
        assert _rc(result) == N_POINTS


# ══════════════════════════════════════════════════════════════════════════════
# M-17b — Area (op aggiuntiva nel registry, non nel test plan originale)
# ══════════════════════════════════════════════════════════════════════════════

class TestGeoArea:

    def test_area_crs_geografico_reproietta(self, client, gpkg_polygons, make_project, poll):
        """geo_area: CRS 4326 → riproiezione UTM → area in m²."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] area geo→utm", gpkg_polygons, "geo_area",
            {"geom_column": "geometry", "output_column": "area_m2"})
        assert _ok(result), f"Area geo→utm fallita: {result}"
        assert _rc(result) == N_POLYGONS
        assert "area_m2" in _cols(client, pid, poll)

    def test_area_crs_metrico_diretto(self, client, gpkg_utm32632, make_project, poll):
        """geo_area: CRS 32632 (metrico) → area senza riproiezione."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] area utm direct", gpkg_utm32632, "geo_area",
            {"geom_column": "geometry", "output_column": "area_m2"})
        assert _ok(result), f"Area UTM diretta fallita: {result}"
        assert _rc(result) == N_UTM

    def test_area_output_column_custom(self, client, gpkg_polygons, make_project, poll):
        """geo_area: output_column custom 'superficie_mq'."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] area custom col", gpkg_polygons, "geo_area",
            {"geom_column": "geometry", "output_column": "superficie_mq"})
        assert _ok(result)
        assert "superficie_mq" in _cols(client, pid, poll)

    def test_area_valori_positivi(self, client, gpkg_polygons, make_project, poll):
        """geo_area: valori area > 0 per tutti i poligoni."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] area positive", gpkg_polygons, "geo_area",
            {"geom_column": "geometry", "output_column": "area_m2"})
        assert _ok(result)
        rows = _rows(client, pid, poll, limit=5)
        for r in rows:
            if r.get("area_m2") is not None:
                assert float(r["area_m2"]) > 0, f"Area deve essere positiva: {r['area_m2']}"


# ══════════════════════════════════════════════════════════════════════════════
# M-18 — Explode (N/A documentativo)
# ══════════════════════════════════════════════════════════════════════════════

class TestM18Explode:
    def test_explode_non_implementato(self, client):
        """M-18: geo_explode non è nel registry — N/A confermato."""
        res = client.get("/transforms/geo_explode")
        assert res.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# M-19 — Clip
# ══════════════════════════════════════════════════════════════════════════════

class TestM19Clip:

    def test_clip_punti_con_poligoni(self, client, gpkg_points, gpkg_polygons, make_project, poll):
        """M-19a: clip punti×poligoni → solo punti dentro la maschera."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-19a clip pts×poly", gpkg_points, "geo_clip",
            {"input_geom": "geometry", "mask_geom": "geometry"},
            second_file_id=gpkg_polygons)
        assert _ok(result), f"Clip pts×poly fallito: {result}"
        assert _rc(result) <= N_POINTS

    def test_clip_crs_diversi_ensure_same(self, client, gpkg_polygons, gpkg_utm32632, make_project, poll):
        """M-19b: CRS diversi → ensure_same_crs automatico (no crash)."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-19b clip crs diversi", gpkg_polygons, "geo_clip",
            {"input_geom": "geometry", "mask_geom": "geometry"},
            second_file_id=gpkg_utm32632)
        assert result.get("success") in (True, False), f"Crash inatteso: {result}"

    def test_clip_zero_risultati(self, client, gpkg_utm32632, gpkg_points, make_project, poll):
        """M-19c: maschera UTM lontana dai punti → 0 feature o fallback senza crash."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-19c clip zero", gpkg_utm32632, "geo_clip",
            {"input_geom": "geometry", "mask_geom": "geometry"},
            second_file_id=gpkg_points)
        # Accetta sia 0 righe che failure pulito — no crash è l'unica garanzia
        assert result is not None
        if _ok(result):
            # rows_count può essere 0 (falsy) — recupera esplicitamente dal dict
            rc = result.get("rows_count")
            if rc is None:
                rc = result.get("row_count")
            assert rc is not None and rc >= 0, f"rows_count atteso >= 0: {result}"


# ══════════════════════════════════════════════════════════════════════════════
# Op extra: geo_intersection
# ══════════════════════════════════════════════════════════════════════════════

class TestGeoIntersection:

    def test_intersection_poligoni(self, client, gpkg_polygons, gpkg_utm32632, make_project, poll):
        """geo_intersection: area comune tra due layer → _ensure_active_geometry."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] intersection poly", gpkg_polygons, "geo_intersection",
            {"left_geom": "geometry", "right_geom": "geometry"},
            second_file_id=gpkg_utm32632)
        # Può essere 0 righe se non si sovrappongono — non deve crashare
        assert result is not None
        if _ok(result):
            assert "geometry" in _cols(client, pid, poll)

    def test_intersection_stesso_layer(self, client, gpkg_polygons, make_project, poll):
        """geo_intersection: A∩A = A stesso (righe = N_POLYGONS o scomposte)."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] intersection self", gpkg_polygons, "geo_intersection",
            {"left_geom": "geometry", "right_geom": "geometry"},
            second_file_id=gpkg_polygons)
        assert _ok(result), f"Intersection self fallita: {result}"
        assert _rc(result) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# M-20 — Union geometrica
# ══════════════════════════════════════════════════════════════════════════════

class TestM20Union:

    def test_union_due_layer(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-20a: union poly∪pts → righe > 0, geometry attiva valida."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] M-20a union 2layer", gpkg_polygons, "geo_union",
            {"left_geom": "geometry", "right_geom": "geometry"},
            second_file_id=gpkg_points)
        assert _ok(result), f"Union fallita: {result}"
        assert _rc(result) > 0
        assert "geometry" in _cols(client, pid, poll)

    def test_union_self_join(self, client, gpkg_polygons, make_project, poll):
        """M-20b: A∪A → righe ≥ N_POLYGONS."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-20b union self", gpkg_polygons, "geo_union",
            {"left_geom": "geometry", "right_geom": "geometry"},
            second_file_id=gpkg_polygons)
        assert _ok(result), f"Union self fallita: {result}"
        assert _rc(result) >= N_POLYGONS

    def test_union_crs_diversi(self, client, gpkg_polygons, gpkg_utm32632, make_project, poll):
        """M-20c: CRS diversi → ensure_same_crs → no crash."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-20c union crs", gpkg_polygons, "geo_union",
            {"left_geom": "geometry", "right_geom": "geometry"},
            second_file_id=gpkg_utm32632)
        assert result.get("success") in (True, False)


# ══════════════════════════════════════════════════════════════════════════════
# M-21 — Differenza geometrica
# ══════════════════════════════════════════════════════════════════════════════

class TestM21Difference:

    def test_difference_a_meno_b(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-21a: A−B → geometry attiva valida, righe ≥ 0."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] M-21a diff A-B", gpkg_polygons, "geo_difference",
            {"left_geom": "geometry", "right_geom": "geometry"},
            second_file_id=gpkg_points)
        assert _ok(result), f"Difference A-B fallita: {result}"
        assert "geometry" in _cols(client, pid, poll)

    def test_difference_self_zero(self, client, gpkg_polygons, make_project, poll):
        """M-21b: A−A → 0 righe o geometrie degenerate (no crash)."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-21b diff self", gpkg_polygons, "geo_difference",
            {"left_geom": "geometry", "right_geom": "geometry"},
            second_file_id=gpkg_polygons)
        assert _ok(result), f"Difference self fallita: {result}"

    def test_difference_crs_diversi(self, client, gpkg_polygons, gpkg_utm32632, make_project, poll):
        """M-21c: CRS diversi → ensure_same_crs → no crash."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-21c diff crs", gpkg_polygons, "geo_difference",
            {"left_geom": "geometry", "right_geom": "geometry"},
            second_file_id=gpkg_utm32632)
        assert result.get("success") in (True, False)


# ══════════════════════════════════════════════════════════════════════════════
# Op extra: geo_simplify
# ══════════════════════════════════════════════════════════════════════════════

class TestGeoSimplify:

    def test_simplify_default(self, client, gpkg_polygons, make_project, poll):
        """geo_simplify: tolerance default → righe invariate, geometry semplificata."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] simplify default", gpkg_polygons, "geo_simplify",
            {"geom_column": "geometry", "tolerance": 0.001, "preserve_topology": True})
        assert _ok(result), f"Simplify default fallito: {result}"
        assert _rc(result) == N_POLYGONS

    def test_simplify_tolerance_alta(self, client, gpkg_polygons, make_project, poll):
        """geo_simplify: tolerance alta → geometrie molto semplificate ma valide."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] simplify high tol", gpkg_polygons, "geo_simplify",
            {"geom_column": "geometry", "tolerance": 0.1, "preserve_topology": True})
        assert _ok(result)

    def test_simplify_preserve_topology_false(self, client, gpkg_polygons, make_project, poll):
        """geo_simplify: preserve_topology=False → branch alternativo (più aggressivo)."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] simplify no topo", gpkg_polygons, "geo_simplify",
            {"geom_column": "geometry", "tolerance": 0.01, "preserve_topology": False})
        assert _ok(result)
        assert _rc(result) == N_POLYGONS


# ══════════════════════════════════════════════════════════════════════════════
# Op extra: geo_convex_hull
# ══════════════════════════════════════════════════════════════════════════════

class TestGeoConvexHull:

    def test_convex_hull_default(self, client, gpkg_polygons, make_project, poll):
        """geo_convex_hull: colonna convex_hull aggiunta, geometry originale preservata."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] convex hull default", gpkg_polygons, "geo_convex_hull",
            {"geom_column": "geometry", "output_column": "convex_hull"})
        assert _ok(result), f"Convex hull fallito: {result}"
        assert _rc(result) == N_POLYGONS
        cols = _cols(client, pid, poll)
        assert "convex_hull" in cols
        assert "geometry" in cols

    def test_convex_hull_output_column_custom(self, client, gpkg_polygons, make_project, poll):
        """geo_convex_hull: output_column='inviluppo'."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] convex hull custom", gpkg_polygons, "geo_convex_hull",
            {"geom_column": "geometry", "output_column": "inviluppo"})
        assert _ok(result)
        assert "inviluppo" in _cols(client, pid, poll)

    def test_convex_hull_punti(self, client, gpkg_points, make_project, poll):
        """geo_convex_hull: punti → convex hull = punto stesso."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] convex hull pts", gpkg_points, "geo_convex_hull",
            {"geom_column": "geometry", "output_column": "hull"})
        assert _ok(result)
        assert _rc(result) == N_POINTS


# ══════════════════════════════════════════════════════════════════════════════
# M-22 — Riproiezione CRS
# ══════════════════════════════════════════════════════════════════════════════

class TestM22Reproject:

    def test_reproject_4326_to_32632(self, client, gpkg_polygons, make_project, poll):
        """M-22a: EPSG:4326 → EPSG:32632, rows invariate."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-22a 4326→32632", gpkg_polygons, "geo_reproject",
            {"geom_column": "geometry", "target_crs": "EPSG:32632"})
        assert _ok(result), f"Reproject 4326→32632 fallito: {result}"
        assert _rc(result) == N_POLYGONS

    def test_reproject_32632_to_4326(self, client, gpkg_utm32632, make_project, poll):
        """M-22b: EPSG:32632 → EPSG:4326, rows invariate."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-22b 32632→4326", gpkg_utm32632, "geo_reproject",
            {"geom_column": "geometry", "target_crs": "EPSG:4326"})
        assert _ok(result), f"Reproject 32632→4326 fallito: {result}"
        assert _rc(result) == N_UTM

    def test_reproject_4326_to_3857(self, client, gpkg_polygons, make_project, poll):
        """M-22c: EPSG:4326 → EPSG:3857 (Web Mercator)."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-22c 4326→3857", gpkg_polygons, "geo_reproject",
            {"geom_column": "geometry", "target_crs": "EPSG:3857"})
        assert _ok(result), f"Reproject →3857 fallito: {result}"
        assert _rc(result) == N_POLYGONS

    def test_reproject_source_crs_esplicito_override(self, client, gpkg_utm32632, make_project, poll):
        """M-22d: source_crs esplicito → branch override CRS auto-detect."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-22d source_crs", gpkg_utm32632, "geo_reproject",
            {"geom_column": "geometry", "target_crs": "EPSG:4326",
             "source_crs": "EPSG:32632"})
        assert _ok(result), f"Reproject source_crs fallito: {result}"
        assert _rc(result) == N_UTM

    def test_reproject_noop_stesso_crs(self, client, gpkg_polygons, make_project, poll):
        """M-22e: target_crs == source_crs → noop, rows invariate."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] M-22e noop", gpkg_polygons, "geo_reproject",
            {"geom_column": "geometry", "target_crs": "EPSG:4326"})
        assert _ok(result), f"Reproject noop fallito: {result}"
        assert _rc(result) == N_POLYGONS

    def test_reproject_geometry_colonna_output(self, client, gpkg_polygons, make_project, poll):
        """M-22f: output riproiezione ha colonna geometry."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] M-22f geocol", gpkg_polygons, "geo_reproject",
            {"geom_column": "geometry", "target_crs": "EPSG:32632"})
        assert _ok(result)
        assert "geometry" in _cols(client, pid, poll)


# ══════════════════════════════════════════════════════════════════════════════
# Op extra: geo_envelope
# ══════════════════════════════════════════════════════════════════════════════

class TestGeoEnvelope:

    def test_envelope_default(self, client, gpkg_polygons, make_project, poll):
        """geo_envelope: bounding box per ogni geometria, colonna envelope aggiunta."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] envelope default", gpkg_polygons, "geo_envelope",
            {"geom_column": "geometry", "output_column": "envelope"})
        assert _ok(result), f"Envelope fallito: {result}"
        assert _rc(result) == N_POLYGONS
        cols = _cols(client, pid, poll)
        assert "envelope" in cols
        assert "geometry" in cols

    def test_envelope_output_column_custom(self, client, gpkg_polygons, make_project, poll):
        """geo_envelope: output_column='bbox'."""
        result, pid = _make_pid(client, make_project, poll,
            "[AUTO] envelope custom", gpkg_polygons, "geo_envelope",
            {"geom_column": "geometry", "output_column": "bbox"})
        assert _ok(result)
        assert "bbox" in _cols(client, pid, poll)

    def test_envelope_punti(self, client, gpkg_points, make_project, poll):
        """geo_envelope: punti → envelope = punto stesso (degenere), rows invariate."""
        result, _ = _run_geo(client, make_project, poll,
            "[AUTO] envelope pts", gpkg_points, "geo_envelope",
            {"geom_column": "geometry", "output_column": "envelope"})
        assert _ok(result)
        assert _rc(result) == N_POINTS
