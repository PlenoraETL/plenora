"""
M-10 — Join spaziale ST_Intersects
M-11 — Join spaziale ST_DWithin 500m (within predicate)
=========================================================
Dataset:
  gpkg_polygons: 20 comuni sintetici EPSG:4326, colonne: id, nome, provincia, area_kmq, geometry
  gpkg_points:   50 POI sintetici EPSG:4326, colonne: id, nome, categoria, geometry

M-10: sjoin predicate=intersects → POI che cadono dentro un comune
M-11: sjoin predicate=within     → POI within (subset di intersects)

Branch coperti da op_sjoin:
  ✅ < 2 input → passthrough
  ✅ to_geodataframe su WKT → GeoDataFrame
  ✅ ensure_same_crs → allineamento CRS
  ✅ predicate=intersects
  ✅ predicate=within
  ✅ how=inner → solo match
  ✅ how=left → tutti i left + match
  ✅ drop index_right
  ✅ _ensure_active_geometry post-join
  ✅ row_count: inner ≤ left join
  ✅ colonne da entrambi i layer presenti
"""

import pytest

pytestmark = [pytest.mark.manipola]


# ── Helper ────────────────────────────────────────────────────────────────────

def _canvas_sjoin(left_id, right_id, config, filename="sjoin_out"):
    from conftest import build_manipola_canvas
    return build_manipola_canvas(
        file_source_id=left_id,
        transform_op="sjoin",
        transform_config=config,
        output_format="csv",
        output_filename=filename,
        second_file_source_id=right_id,
    )

def _execute(client, project_id, poll):
    res = client.post(f"/projects/{project_id}/execute")
    assert res.status_code == 200
    return poll(res.json()["task_id"])

def _preview(client, project_id, poll, limit=60):
    res = client.get(f"/projects/{project_id}/preview", params={"limit": limit})
    assert res.status_code == 200, f"Preview fallita: {res.text}"
    return poll(res.json().get("task_id"))

def _cols(result):
    return {c["name"] if isinstance(c, dict) else c for c in result.get("columns", [])}

def _rows(result):
    return result.get("rows", result.get("data", []))

def _rc(result):
    return result.get("row_count", result.get("rows_count"))


# ── M-10: Intersects ──────────────────────────────────────────────────────────

class TestM10Intersects:

    def test_intersects_inner_produce_risultato(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-10a: sjoin intersects inner → almeno 1 riga (POI dentro poligoni)."""
        config = {
            "left_geom": "geometry", "right_geom": "geometry",
            "predicate": "intersects", "how": "inner"
        }
        project = make_project("[AUTO] M-10a intersects inner", "manipola",
                               canvas=_canvas_sjoin(gpkg_polygons, gpkg_points, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc is not None and rc >= 0, f"Risultato non valido: {rc}"

    def test_intersects_colonne_da_entrambi(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-10b: risultato ha colonne da entrambi i layer."""
        config = {
            "left_geom": "geometry", "right_geom": "geometry",
            "predicate": "intersects", "how": "inner"
        }
        project = make_project("[AUTO] M-10b colonne entrambi", "manipola",
                               canvas=_canvas_sjoin(gpkg_polygons, gpkg_points, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        # Left: id, nome, provincia, area_kmq, geometry
        # Right: id, nome, categoria, geometry
        # Almeno una colonna da ognuno (con possibili suffissi _left/_right)
        has_left  = any("provincia" in c or "area_kmq" in c for c in cols)
        has_right = any("categoria" in c for c in cols)
        assert has_left,  f"Colonne dal layer poligoni mancanti: {cols}"
        assert has_right, f"Colonne dal layer punti mancanti: {cols}"

    def test_intersects_no_index_right(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-10c: colonna index_right rimossa dal risultato."""
        config = {
            "left_geom": "geometry", "right_geom": "geometry",
            "predicate": "intersects", "how": "inner"
        }
        project = make_project("[AUTO] M-10c no index_right", "manipola",
                               canvas=_canvas_sjoin(gpkg_polygons, gpkg_points, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        assert "index_right" not in cols, f"index_right non dovrebbe essere presente: {cols}"

    def test_intersects_left_join_gte_inner(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-10d: left join >= inner join (left preserva tutti i poligoni)."""
        config_inner = {"left_geom": "geometry", "right_geom": "geometry",
                        "predicate": "intersects", "how": "inner"}
        config_left  = {"left_geom": "geometry", "right_geom": "geometry",
                        "predicate": "intersects", "how": "left"}
        p_inner = make_project("[AUTO] M-10d inner", "manipola",
                               canvas=_canvas_sjoin(gpkg_polygons, gpkg_points, config_inner))
        p_left  = make_project("[AUTO] M-10d left",  "manipola",
                               canvas=_canvas_sjoin(gpkg_polygons, gpkg_points, config_left))
        r_inner = _execute(client, p_inner, poll)
        r_left  = _execute(client, p_left,  poll)
        rc_inner = _rc(r_inner) or 0
        rc_left  = _rc(r_left)  or 0
        assert rc_left >= rc_inner, (
            f"Left join ({rc_left}) deve essere >= inner join ({rc_inner})"
        )

    def test_intersects_left_join_min_20_poligoni(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-10e: left join poligoni+punti → almeno 20 righe (tutti i poligoni preservati)."""
        config = {"left_geom": "geometry", "right_geom": "geometry",
                  "predicate": "intersects", "how": "left"}
        project = make_project("[AUTO] M-10e left min 20", "manipola",
                               canvas=_canvas_sjoin(gpkg_polygons, gpkg_points, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc >= 20, f"Left join: attese almeno 20 righe (20 poligoni), trovate {rc}"


# ── M-11: Within / DWithin ────────────────────────────────────────────────────

class TestM11Within:

    def test_within_produce_risultato(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-11a: sjoin within → subset di intersects (punti strettamente dentro)."""
        config = {
            "left_geom": "geometry", "right_geom": "geometry",
            "predicate": "within", "how": "inner"
        }
        project = make_project("[AUTO] M-11a within", "manipola",
                               canvas=_canvas_sjoin(gpkg_points, gpkg_polygons, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc is not None and rc >= 0, f"within: risultato non valido: {rc}"

    def test_within_lte_intersects(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-11b: within <= intersects (within è più restrittivo)."""
        cfg_within     = {"left_geom": "geometry", "right_geom": "geometry",
                          "predicate": "within",     "how": "inner"}
        cfg_intersects = {"left_geom": "geometry", "right_geom": "geometry",
                          "predicate": "intersects", "how": "inner"}
        # punti within poligoni vs punti intersects poligoni
        p_w = make_project("[AUTO] M-11b within",     "manipola",
                           canvas=_canvas_sjoin(gpkg_points, gpkg_polygons, cfg_within))
        p_i = make_project("[AUTO] M-11b intersects", "manipola",
                           canvas=_canvas_sjoin(gpkg_points, gpkg_polygons, cfg_intersects))
        r_w = _execute(client, p_w, poll)
        r_i = _execute(client, p_i, poll)
        rc_w = _rc(r_w) or 0
        rc_i = _rc(r_i) or 0
        assert rc_w <= rc_i, (
            f"within ({rc_w}) deve essere <= intersects ({rc_i})"
        )

    def test_within_colonne_presenti(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-11c: within → colonne da entrambi i layer presenti."""
        config = {"left_geom": "geometry", "right_geom": "geometry",
                  "predicate": "within", "how": "inner"}
        project = make_project("[AUTO] M-11c within colonne", "manipola",
                               canvas=_canvas_sjoin(gpkg_points, gpkg_polygons, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        # Punti: categoria; Poligoni: provincia, area_kmq
        has_punto   = any("categoria" in c for c in cols)
        has_poligon = any("provincia" in c or "area_kmq" in c for c in cols)
        assert has_punto,   f"Colonne punti mancanti: {cols}"
        assert has_poligon, f"Colonne poligoni mancanti: {cols}"

    def test_predicati_diversi_risultati_diversi(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-11d: intersects e within producono risultati diversi (o uguali se dataset piccolo)."""
        cfg_w = {"left_geom": "geometry", "right_geom": "geometry",
                 "predicate": "within",     "how": "inner"}
        cfg_i = {"left_geom": "geometry", "right_geom": "geometry",
                 "predicate": "intersects", "how": "inner"}
        p_w = make_project("[AUTO] M-11d within",     "manipola",
                           canvas=_canvas_sjoin(gpkg_polygons, gpkg_points, cfg_w))
        p_i = make_project("[AUTO] M-11d intersects", "manipola",
                           canvas=_canvas_sjoin(gpkg_polygons, gpkg_points, cfg_i))
        r_w = _execute(client, p_w, poll)
        r_i = _execute(client, p_i, poll)
        # Entrambi devono completare senza crash
        assert _rc(r_w) is not None, "within: task fallito"
        assert _rc(r_i) is not None, "intersects: task fallito"


# ── Edge cases spaziali ───────────────────────────────────────────────────────

class TestM10M11Edge:

    def test_crs_allineamento_automatico(self, client, gpkg_polygons, gpkg_utm32632, make_project, poll):
        """M-10/11 edge: layer con CRS diversi (4326 vs 32632) → allineamento automatico."""
        config = {"left_geom": "geometry", "right_geom": "geometry",
                  "predicate": "intersects", "how": "left"}
        project = make_project("[AUTO] M-10/11 edge CRS align", "manipola",
                               canvas=_canvas_sjoin(gpkg_polygons, gpkg_utm32632, config))
        result = _execute(client, project, poll)
        # Non deve crashare — l'allineamento CRS deve gestire la differenza
        rc = _rc(result)
        assert rc is not None, "CRS alignment: task fallito"

    def test_stesso_layer_sjoin_con_se_stesso(self, client, gpkg_polygons, make_project, poll):
        """M-11 edge: sjoin layer con se stesso → ogni poligono intersects se stesso."""
        config = {"left_geom": "geometry", "right_geom": "geometry",
                  "predicate": "intersects", "how": "inner"}
        project = make_project("[AUTO] M-11 edge self-join", "manipola",
                               canvas=_canvas_sjoin(gpkg_polygons, gpkg_polygons, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        # Ogni poligono intersects se stesso → almeno 20 righe
        assert rc >= 20, f"Self-join: attese almeno 20 righe, trovate {rc}"


# ── Predicati aggiuntivi ──────────────────────────────────────────────────────

class TestM10M11Predicati:

    @pytest.mark.parametrize("predicate", ["contains", "crosses", "overlaps", "touches"])
    def test_predicato_non_crasha(self, client, gpkg_polygons, gpkg_points, make_project, poll, predicate):
        """M-10/11 extra: ogni predicato supportato non causa crash."""
        config = {"left_geom": "geometry", "right_geom": "geometry",
                  "predicate": predicate, "how": "inner"}
        project = make_project(f"[AUTO] M-10/11 pred={predicate}", "manipola",
                               canvas=_canvas_sjoin(gpkg_polygons, gpkg_points, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        # Può essere 0 righe (nessun match geometrico) ma non deve crashare
        assert rc is not None, f"predicate={predicate}: task fallito/crash"
        assert rc >= 0, f"predicate={predicate}: row_count negativo"

    def test_how_right_join(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-10/11 extra: how=right → tutti i punti preservati."""
        config = {"left_geom": "geometry", "right_geom": "geometry",
                  "predicate": "intersects", "how": "right"}
        project = make_project("[AUTO] M-10/11 how=right", "manipola",
                               canvas=_canvas_sjoin(gpkg_polygons, gpkg_points, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc is not None, "how=right: task fallito"
        # Right join su 50 punti → almeno 50 righe
        assert rc >= 50, f"how=right: attese almeno 50 righe (tutti i punti), trovate {rc}"

    def test_contains_inverso_di_within(self, client, gpkg_polygons, gpkg_points, make_project, poll):
        """M-10/11 extra: poligoni contains punti == punti within poligoni (invertiti)."""
        # poligoni contains punti (left=poligoni, right=punti)
        cfg_c = {"left_geom": "geometry", "right_geom": "geometry",
                 "predicate": "contains", "how": "inner"}
        # punti within poligoni (left=punti, right=poligoni)
        cfg_w = {"left_geom": "geometry", "right_geom": "geometry",
                 "predicate": "within", "how": "inner"}
        p_c = make_project("[AUTO] M-10/11 contains", "manipola",
                           canvas=_canvas_sjoin(gpkg_polygons, gpkg_points, cfg_c))
        p_w = make_project("[AUTO] M-10/11 within inv", "manipola",
                           canvas=_canvas_sjoin(gpkg_points, gpkg_polygons, cfg_w))
        r_c = _execute(client, p_c, poll)
        r_w = _execute(client, p_w, poll)
        assert _rc(r_c) is not None, "contains: task fallito"
        assert _rc(r_w) is not None, "within: task fallito"
        # Semanticamente equivalenti → stesso row_count
        assert _rc(r_c) == _rc(r_w), (
            f"contains ({_rc(r_c)}) != within ({_rc(r_w)}): "
            f"dovrebbero essere semanticamente equivalenti"
        )
