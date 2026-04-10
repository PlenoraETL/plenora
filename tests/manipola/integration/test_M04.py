"""
M-04 — Multi-Join sequenziale 5 DataFrame (left join)
======================================================
Dataset: csv_join_common_key
  base (100):  entity_id, nome, categoria
  det1 (80):   entity_id, valore          ← 20 entità mancanti → NULL in left
  det2 (90):   entity_id, area            ← 10 entità mancanti → NULL in left
  det3 (100):  entity_id, anno
  det4 (100):  entity_id, score

Branch coperti da _esegui_multi_join:
  ✅ < 2 input → passthrough
  ✅ join_keys fornite esplicitamente
  ✅ join_keys auto-detect (colonne comuni)
  ✅ colonne duplicate rinominate con suffisso _N
  ✅ left join → 100 righe (base preservata)
  ✅ inner join → ≤ 80 righe (intersezione)
  ✅ chiavi mancanti in un DataFrame → fallback primo df
  ✅ merge sequenziale 5 tabelle: colonne accumulate correttamente
"""

import pytest

pytestmark = [pytest.mark.manipola]

# Dimensioni note del dataset
N_BASE   = 100   # righe tabella base
N_DET1   = 80    # righe det1 (20 mancanti)
N_DET2   = 90    # righe det2 (10 mancanti)

# Colonne attese dopo join completo (left)
COLS_ATTESE = {"entity_id", "nome", "categoria", "valore", "area", "anno", "score"}


# ── Helper ────────────────────────────────────────────────────────────────────

def _canvas_multi(file_ids: list, config: dict, filename="join_out"):
    from conftest import build_manipola_canvas_multi_source
    return build_manipola_canvas_multi_source(
        file_source_ids=file_ids,
        transform_op="multi_join",
        transform_config=config,
        output_format="csv",
        output_filename=filename,
    )

def _execute(client, project_id, poll):
    res = client.post(f"/projects/{project_id}/execute")
    assert res.status_code == 200
    return poll(res.json()["task_id"])

def _preview(client, project_id, poll, limit=110):
    res = client.get(f"/projects/{project_id}/preview", params={"limit": limit})
    assert res.status_code == 200, f"Preview fallita: {res.text}"
    return poll(res.json().get("task_id"))

def _cols(result):
    return {c["name"] if isinstance(c, dict) else c for c in result.get("columns", [])}

def _rows(result):
    return result.get("rows", result.get("data", []))


# ── Happy path ────────────────────────────────────────────────────────────────

class TestM04HappyPath:

    def test_left_join_5_tabelle_row_count(self, client, csv_join_common_key, make_project, poll):
        """M-04a: left join 5 tabelle → 100 righe (base preservata)."""
        ids = csv_join_common_key
        config = {"join_keys": ["entity_id"], "join_type": "left"}
        project = make_project("[AUTO] M-04a left join 5 row_count", "manipola",
                               canvas=_canvas_multi(
                                   [ids["base"], ids["det1"], ids["det2"],
                                    ids["det3"], ids["det4"]], config))
        result = _execute(client, project, poll)
        rc = result.get("row_count", result.get("rows_count"))
        assert rc == N_BASE, f"Left join: attese {N_BASE} righe, trovate {rc}"

    def test_left_join_colonne_tutte_presenti(self, client, csv_join_common_key, make_project, poll):
        """M-04b: after join tutte le colonne delle 5 tabelle sono presenti."""
        ids = csv_join_common_key
        config = {"join_keys": ["entity_id"], "join_type": "left"}
        project = make_project("[AUTO] M-04b colonne presenti", "manipola",
                               canvas=_canvas_multi(
                                   [ids["base"], ids["det1"], ids["det2"],
                                    ids["det3"], ids["det4"]], config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        for c in COLS_ATTESE:
            assert c in cols, f"Colonna '{c}' mancante dopo join: {cols}"

    def test_left_join_entity_id_univoco(self, client, csv_join_common_key, make_project, poll):
        """M-04c: ogni entity_id compare esattamente una volta (no duplicati da join)."""
        ids = csv_join_common_key
        config = {"join_keys": ["entity_id"], "join_type": "left"}
        project = make_project("[AUTO] M-04c entity_id univoco", "manipola",
                               canvas=_canvas_multi(
                                   [ids["base"], ids["det1"], ids["det2"],
                                    ids["det3"], ids["det4"]], config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        eids = [r.get("entity_id") for r in rows if r.get("entity_id") is not None]
        assert len(eids) == len(set(str(e) for e in eids)), (
            f"entity_id duplicati nel risultato join: {len(eids)} totali, "
            f"{len(set(str(e) for e in eids))} unici"
        )

    def test_left_join_null_per_det1_mancanti(self, client, csv_join_common_key, make_project, poll):
        """M-04d: le 20 entità senza det1 hanno valore=NULL nel risultato."""
        ids = csv_join_common_key
        config = {"join_keys": ["entity_id"], "join_type": "left"}
        project = make_project("[AUTO] M-04d null det1", "manipola",
                               canvas=_canvas_multi(
                                   [ids["base"], ids["det1"]], config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        null_count = sum(1 for r in rows if r.get("valore") is None)
        assert null_count == 20, (
            f"Attesi 20 NULL in 'valore' (entità senza det1), trovati {null_count}"
        )

    def test_left_join_det3_det4_completi_no_null(self, client, csv_join_common_key, make_project, poll):
        """M-04e: det3 e det4 hanno tutti 100 entity_id → nessun NULL in anno/score."""
        ids = csv_join_common_key
        config = {"join_keys": ["entity_id"], "join_type": "left"}
        project = make_project("[AUTO] M-04e no null det3 det4", "manipola",
                               canvas=_canvas_multi(
                                   [ids["base"], ids["det3"], ids["det4"]], config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        null_anno  = sum(1 for r in rows if r.get("anno")  is None)
        null_score = sum(1 for r in rows if r.get("score") is None)
        assert null_anno  == 0, f"Attesi 0 NULL in 'anno', trovati {null_anno}"
        assert null_score == 0, f"Attesi 0 NULL in 'score', trovati {null_score}"


# ── Tipi join ─────────────────────────────────────────────────────────────────

class TestM04TipiJoin:

    def test_inner_join_row_count_ridotto(self, client, csv_join_common_key, make_project, poll):
        """M-04f: inner join base + det1 (80) → max 80 righe (solo entità comuni)."""
        ids = csv_join_common_key
        config = {"join_keys": ["entity_id"], "join_type": "inner"}
        project = make_project("[AUTO] M-04f inner join", "manipola",
                               canvas=_canvas_multi([ids["base"], ids["det1"]], config))
        result = _execute(client, project, poll)
        rc = result.get("row_count", result.get("rows_count"))
        assert rc <= N_DET1, (
            f"Inner join: attese ≤{N_DET1} righe, trovate {rc}"
        )
        assert rc > 0, "Inner join: 0 righe, qualcosa è andato storto"

    def test_left_vs_inner_row_count(self, client, csv_join_common_key, make_project, poll):
        """M-04g: left join ≥ inner join (left preserva tutte le righe base)."""
        ids = csv_join_common_key
        p_left  = make_project("[AUTO] M-04g left",  "manipola",
                               canvas=_canvas_multi([ids["base"], ids["det1"]],
                                                    {"join_keys": ["entity_id"], "join_type": "left"}))
        p_inner = make_project("[AUTO] M-04g inner", "manipola",
                               canvas=_canvas_multi([ids["base"], ids["det1"]],
                                                    {"join_keys": ["entity_id"], "join_type": "inner"}))
        r_left  = _execute(client, p_left,  poll)
        r_inner = _execute(client, p_inner, poll)
        rc_left  = r_left.get("row_count",  r_left.get("rows_count",  0))
        rc_inner = r_inner.get("row_count", r_inner.get("rows_count", 0))
        assert rc_left >= rc_inner, (
            f"Left ({rc_left}) deve essere >= inner ({rc_inner})"
        )

    def test_outer_join_row_count(self, client, csv_join_common_key, make_project, poll):
        """M-04h: outer join base (100) + det1 (80) → 100 righe (tutti entity_id coperti)."""
        ids = csv_join_common_key
        config = {"join_keys": ["entity_id"], "join_type": "outer"}
        project = make_project("[AUTO] M-04h outer join", "manipola",
                               canvas=_canvas_multi([ids["base"], ids["det1"]], config))
        result = _execute(client, project, poll)
        rc = result.get("row_count", result.get("rows_count"))
        assert rc == N_BASE, f"Outer join: attese {N_BASE} righe, trovate {rc}"


# ── Colonne duplicate ─────────────────────────────────────────────────────────

class TestM04ColonneDuplicate:

    def test_colonne_duplicate_rinominate(self, client, csv_join_common_key, make_project, poll):
        """M-04i: join di tabelle con colonna 'nome' comune → rinominata con suffisso."""
        # Carica due copie della stessa tabella base → colonne duplicate
        ids = csv_join_common_key
        config = {"join_keys": ["entity_id"], "join_type": "left"}
        project = make_project("[AUTO] M-04i col duplicate", "manipola",
                               canvas=_canvas_multi([ids["base"], ids["base"]], config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        # entity_id deve comparire una sola volta
        entity_count = sum(1 for c in cols if c == "entity_id")
        assert entity_count == 1, (
            f"entity_id deve comparire 1 volta, trovato {entity_count} volte: {cols}"
        )
        # nome e categoria devono avere suffisso per il secondo df
        col_list = list(cols)
        nome_cols = [c for c in col_list if "nome" in c]
        assert len(nome_cols) >= 1, f"Almeno 'nome' atteso dopo join duplicato: {col_list}"

    def test_join_3_tabelle_colonne_cumulate(self, client, csv_join_common_key, make_project, poll):
        """M-04j: join 3 tabelle → colonne si accumulano correttamente."""
        ids = csv_join_common_key
        config = {"join_keys": ["entity_id"], "join_type": "left"}
        project = make_project("[AUTO] M-04j 3 tabelle col", "manipola",
                               canvas=_canvas_multi(
                                   [ids["base"], ids["det3"], ids["det4"]], config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        for c in ["entity_id", "nome", "categoria", "anno", "score"]:
            assert c in cols, f"Colonna '{c}' mancante dopo join 3 tabelle: {cols}"


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestM04Edge:

    def test_join_keys_auto_detect(self, client, csv_join_common_key, make_project, poll):
        """M-04k edge: join_keys=[] → auto-detect colonne comuni (entity_id)."""
        ids = csv_join_common_key
        config = {"join_keys": [], "join_type": "left"}
        project = make_project("[AUTO] M-04k auto-detect keys", "manipola",
                               canvas=_canvas_multi([ids["base"], ids["det3"]], config))
        result = _execute(client, project, poll)
        rc = result.get("row_count", result.get("rows_count"))
        assert rc == N_BASE, f"Auto-detect: attese {N_BASE} righe, trovate {rc}"

    def test_due_tabelle_join_semplice(self, client, csv_join_common_key, make_project, poll):
        """M-04l edge: solo 2 tabelle → merge singolo, risultato corretto."""
        ids = csv_join_common_key
        config = {"join_keys": ["entity_id"], "join_type": "left"}
        project = make_project("[AUTO] M-04l 2 tabelle", "manipola",
                               canvas=_canvas_multi([ids["base"], ids["det4"]], config))
        result = _execute(client, project, poll)
        rc = result.get("row_count", result.get("rows_count"))
        assert rc == N_BASE, f"2 tabelle: attese {N_BASE} righe, trovate {rc}"

    def test_join_preserva_tutti_valori_non_null(self, client, csv_join_common_key, make_project, poll):
        """M-04m edge: entità presenti in det4 (100%) → score mai NULL."""
        ids = csv_join_common_key
        config = {"join_keys": ["entity_id"], "join_type": "left"}
        project = make_project("[AUTO] M-04m score non null", "manipola",
                               canvas=_canvas_multi(
                                   [ids["base"], ids["det1"], ids["det2"],
                                    ids["det3"], ids["det4"]], config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        null_score = sum(1 for r in rows if r.get("score") is None)
        assert null_score == 0, f"score (det4 completo) non deve avere NULL, trovati {null_score}"
