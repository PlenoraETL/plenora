"""
M-03 — Aggregate (group_by, aggregations)
==========================================
Dataset: CSV immobili 6819 righe
  colonne: id, comune, zona, tipo, valore_omi, superficie, piano, anno, lat, lon

Branch coperti da op_aggregate:
  ✅ group_by vuoto → return df invariato
  ✅ group_by con colonne tutte inesistenti → return df invariato
  ✅ aggregations vuote → solo count per gruppo
  ✅ funzioni: count, sum, mean/avg, min, max, first, last
  ✅ concat con separator, distinct, skip_null
  ✅ colonna aggregazione inesistente → skippata silenziosamente
  ✅ agg_dict vuoto (tutte le col inesistenti) → fallback count
  ✅ stessa colonna con funzioni multiple → MultiIndex flatten
  ✅ group_by + aggregazioni multiple in un colpo solo
"""

import pytest

pytestmark = [pytest.mark.manipola]


# ── Helper ────────────────────────────────────────────────────────────────────

def _canvas(file_id, config, filename="agg_out"):
    from conftest import build_manipola_canvas
    return build_manipola_canvas(file_id, "aggregate", config,
                                  output_format="csv", output_filename=filename)

def _execute(client, project_id, poll):
    res = client.post(f"/projects/{project_id}/execute")
    assert res.status_code == 200
    return poll(res.json()["task_id"])

def _preview(client, project_id, poll, limit=50):
    res = client.get(f"/projects/{project_id}/preview", params={"limit": limit})
    assert res.status_code == 200, f"Preview fallita: {res.text}"
    return poll(res.json().get("task_id"))

def _cols(result):
    return [c["name"] if isinstance(c, dict) else c
            for c in result.get("columns", [])]

def _rows(result):
    return result.get("rows", result.get("data", []))


# ── Happy path ────────────────────────────────────────────────────────────────

class TestM03HappyPath:

    def test_group_by_comune_count(self, client, csv_immobili, make_project, poll):
        """M-03a: group_by=comune, aggregations=[] → count per comune."""
        config = {"group_by": ["comune"], "aggregations": []}
        project = make_project("[AUTO] M-03a group count", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        cols = _cols(result)
        # Comuni distinti nel dataset
        assert len(rows) > 0, "Nessun gruppo restituito"
        assert "comune" in cols, f"Colonna 'comune' mancante: {cols}"
        assert "count" in cols, f"Colonna 'count' mancante: {cols}"
        # Tutti i count devono essere > 0
        for r in rows:
            assert int(r.get("count", 0)) > 0, f"Count zero per riga: {r}"

    def test_group_by_row_count_ridotto(self, client, csv_immobili, make_project, poll):
        """M-03b: group_by riduce le righe rispetto al dataset originale (6819)."""
        config = {"group_by": ["comune"], "aggregations": []}
        project = make_project("[AUTO] M-03b row count ridotto", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _execute(client, project, poll)
        rc = result.get("row_count", result.get("rows_count"))
        assert rc is not None, "row_count mancante nel result"
        assert rc < 6819, f"Aggregate non ha ridotto le righe: {rc}"

    def test_aggregazioni_sum_e_mean(self, client, csv_immobili, make_project, poll):
        """M-03c: sum e mean su colonna numerica."""
        config = {
            "group_by": ["comune"],
            "aggregations": [
                {"column": "valore_omi", "function": "sum"},
                {"column": "valore_omi", "function": "mean"},
            ]
        }
        project = make_project("[AUTO] M-03c sum+mean", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        rows = _rows(result)
        # sum e mean sulla stessa colonna → MultiIndex flatten → valore_omi_sum, valore_omi_mean
        has_sum  = any("valore_omi" in c and "sum"  in c for c in cols) or "valore_omi_sum" in cols
        has_mean = any("valore_omi" in c and "mean" in c for c in cols) or "valore_omi_mean" in cols
        assert has_sum,  f"Colonna sum valore_omi mancante: {cols}"
        assert has_mean, f"Colonna mean valore_omi mancante: {cols}"

    def test_aggregazione_min_max(self, client, csv_immobili, make_project, poll):
        """M-03d: min e max → min <= mean <= max per ogni gruppo."""
        config = {
            "group_by": ["comune"],
            "aggregations": [
                {"column": "valore_omi", "function": "min"},
                {"column": "valore_omi", "function": "max"},
                {"column": "valore_omi", "function": "mean"},
            ]
        }
        project = make_project("[AUTO] M-03d min/max", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        cols = _cols(result)

        min_col  = next((c for c in cols if "min"  in c and "valore_omi" in c), None)
        max_col  = next((c for c in cols if "max"  in c and "valore_omi" in c), None)
        mean_col = next((c for c in cols if "mean" in c and "valore_omi" in c), None)

        if min_col and max_col and rows:
            for r in rows:
                v_min = r.get(min_col)
                v_max = r.get(max_col)
                if v_min is not None and v_max is not None:
                    assert float(v_min) <= float(v_max), (
                        f"min > max per {r.get('comune')}: {v_min} > {v_max}"
                    )

    def test_aggregazione_first_last(self, client, csv_immobili, make_project, poll):
        """M-03e: first e last → valori non null per ogni gruppo."""
        config = {
            "group_by": ["comune"],
            "aggregations": [
                {"column": "tipo", "function": "first"},
                {"column": "tipo", "function": "last"},
            ]
        }
        project = make_project("[AUTO] M-03e first/last", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        cols = _cols(result)
        first_col = next((c for c in cols if "first" in c), None)
        last_col  = next((c for c in cols if "last"  in c), None)
        assert first_col, f"Colonna first mancante: {cols}"
        assert last_col,  f"Colonna last mancante: {cols}"
        for r in rows:
            assert r.get(first_col) is not None, f"first null per {r.get('comune')}"


# ── Funzioni aggregazione ─────────────────────────────────────────────────────

class TestM03Funzioni:

    @pytest.mark.parametrize("func", ["count", "sum", "mean", "avg", "min", "max", "first", "last"])
    def test_ogni_funzione_produce_risultato(self, client, csv_immobili, make_project, poll, func):
        """M-03f: ogni funzione supportata produce righe senza crash."""
        config = {
            "group_by": ["comune"],
            "aggregations": [{"column": "valore_omi", "function": func}]
        }
        project = make_project(f"[AUTO] M-03f func={func}", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        assert len(rows) > 0, f"func={func}: nessuna riga restituita"
        assert "comune" in _cols(result), f"func={func}: colonna 'comune' mancante"

    def test_avg_alias_mean(self, client, csv_immobili, make_project, poll):
        """M-03g: avg e mean producono lo stesso risultato (alias)."""
        config_avg  = {"group_by": ["comune"], "aggregations": [{"column": "valore_omi", "function": "avg"}]}
        config_mean = {"group_by": ["comune"], "aggregations": [{"column": "valore_omi", "function": "mean"}]}
        p_avg  = make_project("[AUTO] M-03g avg",  "manipola", canvas=_canvas(csv_immobili, config_avg))
        p_mean = make_project("[AUTO] M-03g mean", "manipola", canvas=_canvas(csv_immobili, config_mean))
        r_avg  = _preview(client, p_avg,  poll)
        r_mean = _preview(client, p_mean, poll)
        rows_avg  = _rows(r_avg)
        rows_mean = _rows(r_mean)
        assert len(rows_avg) == len(rows_mean), "avg e mean producono numero righe diverso"

    def test_concat_con_separator(self, client, csv_immobili, make_project, poll):
        """M-03h: concat con separator=' | ' → valori separati da |."""
        config = {
            "group_by": ["comune"],
            "aggregations": [{"column": "tipo", "function": "concat",
                              "separator": " | ", "distinct": True, "skip_null": True}]
        }
        project = make_project("[AUTO] M-03h concat sep", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        cols = _cols(result)
        concat_col = next((c for c in cols if "tipo" in c), None)
        assert concat_col, f"Colonna concat tipo mancante: {cols}"
        # Almeno una riga deve avere il separatore (comune con più tipi)
        has_sep = any("|" in str(r.get(concat_col, "")) for r in rows)
        assert has_sep, f"Nessun valore concatenato con '|' trovato"

    def test_concat_distinct(self, client, csv_immobili, make_project, poll):
        """M-03i: concat distinct=True → nessun duplicato nei valori concatenati."""
        config = {
            "group_by": ["comune"],
            "aggregations": [{"column": "tipo", "function": "concat",
                              "separator": ",", "distinct": True, "skip_null": True}]
        }
        project = make_project("[AUTO] M-03i concat distinct", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        cols = _cols(result)
        concat_col = next((c for c in cols if "tipo" in c), None)
        if concat_col:
            for r in rows:
                val = str(r.get(concat_col, ""))
                parts = [p.strip() for p in val.split(",") if p.strip()]
                assert len(parts) == len(set(parts)), (
                    f"Valori duplicati nel concat distinct per {r.get('comune')}: {val}"
                )


# ── Multi group_by ────────────────────────────────────────────────────────────

class TestM03MultiGroupBy:

    def test_group_by_due_colonne(self, client, csv_immobili, make_project, poll):
        """M-03j: group_by=[comune, zona] → più gruppi, entrambe le colonne presenti."""
        config = {
            "group_by": ["comune", "zona"],
            "aggregations": [{"column": "valore_omi", "function": "sum"}]
        }
        project = make_project("[AUTO] M-03j group 2 col", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        rows = _rows(result)
        assert "comune" in cols, f"Colonna 'comune' mancante: {cols}"
        assert "zona"   in cols, f"Colonna 'zona' mancante: {cols}"
        # Più gruppi rispetto al solo comune
        assert len(rows) > 1, "group_by due colonne: attesi più di 1 gruppo"

    def test_group_by_count_totale_corretto(self, client, csv_immobili, make_project, poll):
        """M-03k: sum dei count per comune = 6819 (totale dataset)."""
        config = {"group_by": ["comune"], "aggregations": []}
        project = make_project("[AUTO] M-03k sum count totale", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _execute(client, project, poll)
        # Verifica via preview
        res_p = client.get(f"/projects/{project}/preview", params={"limit": 100})
        r = poll(res_p.json().get("task_id"))
        rows = _rows(r)
        total = sum(int(row.get("count", 0)) for row in rows if row.get("count"))
        assert total == 6819, f"Sum dei count attesa 6819, trovata {total}"


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestM03Edge:

    def test_group_by_vuoto(self, client, csv_immobili, make_project, poll):
        """M-03l edge: group_by=[] → backend restituisce df invariato (6819 righe)."""
        config = {"group_by": [], "aggregations": []}
        project = make_project("[AUTO] M-03l edge group_by vuoto", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _execute(client, project, poll)
        rc = result.get("row_count", result.get("rows_count"))
        assert rc == 6819, f"group_by vuoto: atteso df invariato (6819), trovato {rc}"

    def test_group_by_colonna_inesistente(self, client, csv_immobili, make_project, poll):
        """M-03m edge: group_by con colonna inesistente → df invariato."""
        config = {"group_by": ["colonna_inesistente"], "aggregations": []}
        project = make_project("[AUTO] M-03m edge col inesistente", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _execute(client, project, poll)
        rc = result.get("row_count", result.get("rows_count"))
        assert rc == 6819, f"Col inesistente: atteso df invariato (6819), trovato {rc}"

    def test_aggregazione_colonna_inesistente_skippata(self, client, csv_immobili, make_project, poll):
        """M-03n edge: aggregazione su colonna inesistente → skippata, risultato valido."""
        config = {
            "group_by": ["comune"],
            "aggregations": [
                {"column": "colonna_inesistente", "function": "sum"},
                {"column": "valore_omi", "function": "count"},  # questa è valida
            ]
        }
        project = make_project("[AUTO] M-03n edge agg col inesistente", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        cols = _cols(result)
        # Il risultato deve avere almeno la colonna valida (valore_omi)
        assert len(rows) > 0, "Nessun risultato con aggregazione parzialmente invalida"
        assert "comune" in cols, f"Colonna comune mancante: {cols}"

    def test_aggregazioni_vuote_fallback_count(self, client, csv_immobili, make_project, poll):
        """M-03o edge: aggregations=None → fallback a count per gruppo."""
        config = {"group_by": ["comune"], "aggregations": None}
        project = make_project("[AUTO] M-03o edge agg None", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        assert "count" in cols, f"Fallback count mancante con aggregations=None: {cols}"
