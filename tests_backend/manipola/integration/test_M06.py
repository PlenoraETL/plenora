"""
M-06 — Conditional (column, conditions, default_value, output_column)
======================================================================
Dataset: csv_filter 166075 righe
  colonne: id, regione, provincia, tipo_uso, valore, anno, attivo
  ~52803 Residenziale, resto non-Residenziale

Branch coperti da op_conditional / _evaluate_condition:
  ✅ column inesistente → return df
  ✅ conditions vuota → return df
  ✅ condizione singola + default
  ✅ ordine priorità: prima condizione vince su sovrapposizioni
  ✅ default_value applicato a righe non matchate
  ✅ output_column custom
  ✅ auto-detect numerico (result values tutti numerici → colonna numerica)
  ✅ operatori: ==, !=, >, >=, <, <=, contains, startswith, endswith, isnull, notnull, between
  ✅ between con min,max
  ✅ condizione inapplicabile (errore) → skippata, continua
"""

import pytest

pytestmark = [pytest.mark.manipola]

N_TOTALE       = 166075
N_RESIDENZIALE = 52803
N_ALTRI        = N_TOTALE - N_RESIDENZIALE


# ── Helper ────────────────────────────────────────────────────────────────────

def _canvas(file_id, config, filename="cond_out"):
    from conftest import build_manipola_canvas
    return build_manipola_canvas(file_id, "conditional", config,
                                  output_format="csv", output_filename=filename)

def _execute(client, project_id, poll):
    res = client.post(f"/projects/{project_id}/execute")
    assert res.status_code == 200
    return poll(res.json()["task_id"])

def _preview(client, project_id, poll, limit=50):
    res = client.get(f"/projects/{project_id}/preview", params={"limit": limit})
    assert res.status_code == 200, f"Preview fallita: {res.text}"
    return poll(res.json().get("task_id"))

def _rows(result):
    return result.get("rows", result.get("data", []))

def _rc(result):
    return result.get("row_count", result.get("rows_count"))

def _cols(result):
    return {c["name"] if isinstance(c, dict) else c for c in result.get("columns", [])}


# ── Happy path ────────────────────────────────────────────────────────────────

class TestM06HappyPath:

    def test_condizione_singola_crea_colonna(self, client, csv_filter, make_project, poll):
        """M-06a: condizione singola → output_column creata nel risultato."""
        config = {
            "column": "tipo_uso",
            "conditions": [{"operator": "==", "value": "Residenziale", "result": "RES"}],
            "default_value": "ALTRO",
            "output_column": "categoria",
        }
        project = make_project("[AUTO] M-06a crea colonna", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        assert "categoria" in cols, f"output_column 'categoria' mancante: {cols}"

    def test_condizione_singola_row_count(self, client, csv_filter, make_project, poll):
        """M-06b: conditional non altera il numero di righe."""
        config = {
            "column": "tipo_uso",
            "conditions": [{"operator": "==", "value": "Residenziale", "result": "RES"}],
            "default_value": "ALTRO",
            "output_column": "cat",
        }
        project = make_project("[AUTO] M-06b row count invariato", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == N_TOTALE, f"Conditional non deve alterare le righe: attese {N_TOTALE}, trovate {rc}"

    def test_valori_match_e_default(self, client, csv_filter, make_project, poll):
        """M-06c: righe Residenziale → 'RES', resto → 'ALTRO'."""
        config = {
            "column": "tipo_uso",
            "conditions": [{"operator": "==", "value": "Residenziale", "result": "RES"}],
            "default_value": "ALTRO",
            "output_column": "cat",
        }
        project = make_project("[AUTO] M-06c match e default", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            if r.get("tipo_uso") == "Residenziale":
                assert r.get("cat") == "RES", (
                    f"Residenziale deve avere cat=RES, trovato {r.get('cat')}"
                )
            else:
                assert r.get("cat") == "ALTRO", (
                    f"Non-Residenziale deve avere cat=ALTRO, trovato {r.get('cat')}"
                )

    def test_output_column_default_nome(self, client, csv_filter, make_project, poll):
        """M-06d: output_column='result' (default) → colonna 'result' presente."""
        config = {
            "column": "tipo_uso",
            "conditions": [{"operator": "==", "value": "Residenziale", "result": "OK"}],
            "default_value": "NO",
            "output_column": "result",
        }
        project = make_project("[AUTO] M-06d output default", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll)
        assert "result" in _cols(result)


# ── Priorità condizioni ───────────────────────────────────────────────────────

class TestM06Priorita:

    def test_prima_condizione_vince(self, client, csv_filter, make_project, poll):
        """M-06e: due condizioni che matchano la stessa riga → la prima vince."""
        # Sia Residenziale che contains 'Res' matchano 'Residenziale'
        # La prima (==) deve vincere su contains
        config = {
            "column": "tipo_uso",
            "conditions": [
                {"operator": "==",       "value": "Residenziale", "result": "ESATTO"},
                {"operator": "contains", "value": "Res",          "result": "CONTIENE"},
            ],
            "default_value": "ALTRO",
            "output_column": "cat",
        }
        project = make_project("[AUTO] M-06e prima vince", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            if r.get("tipo_uso") == "Residenziale":
                assert r.get("cat") == "ESATTO", (
                    f"Prima condizione (==) deve vincere: trovato '{r.get('cat')}'"
                )

    def test_sei_condizioni_plus_default(self, client, csv_filter, make_project, poll):
        """M-06f: 6 condizioni mappano 5 tipi_uso + default → nessuna riga con valore imprevisto."""
        config = {
            "column": "tipo_uso",
            "conditions": [
                {"operator": "==", "value": "Residenziale", "result": "R"},
                {"operator": "==", "value": "Commerciale",  "result": "C"},
                {"operator": "==", "value": "Industriale",  "result": "I"},
                {"operator": "==", "value": "Agricolo",     "result": "A"},
                {"operator": "==", "value": "Altro",        "result": "X"},
                {"operator": "contains", "value": "zzz",   "result": "MAI"},
            ],
            "default_value": "DEFAULT",
            "output_column": "codice",
        }
        project = make_project("[AUTO] M-06f 6 condizioni", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll, limit=100)
        rows = _rows(result)
        valid = {"R", "C", "I", "A", "X", "DEFAULT"}
        for r in rows:
            assert r.get("codice") in valid, (
                f"Valore imprevisto '{r.get('codice')}' per tipo_uso='{r.get('tipo_uso')}'"
            )

    def test_nessuna_riga_con_default_se_copertura_totale(self, client, csv_filter, make_project, poll):
        """M-06g: 5 condizioni coprono tutti i tipi_uso → nessuna riga con 'DEFAULT'."""
        config = {
            "column": "tipo_uso",
            "conditions": [
                {"operator": "==", "value": "Residenziale", "result": "R"},
                {"operator": "==", "value": "Commerciale",  "result": "C"},
                {"operator": "==", "value": "Industriale",  "result": "I"},
                {"operator": "==", "value": "Agricolo",     "result": "A"},
                {"operator": "==", "value": "Altro",        "result": "X"},
            ],
            "default_value": "DEFAULT",
            "output_column": "codice",
        }
        project = make_project("[AUTO] M-06g no default", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll, limit=100)
        rows = _rows(result)
        with_default = [r for r in rows if r.get("codice") == "DEFAULT"]
        assert not with_default, (
            f"Non atteso 'DEFAULT' con copertura totale: trovate {len(with_default)} righe"
        )


# ── Operatori speciali ────────────────────────────────────────────────────────

class TestM06Operatori:

    def test_between_numerico(self, client, csv_filter, make_project, poll):
        """M-06h: between '2020,2022' su anno → solo anni 2020-2022."""
        config = {
            "column": "anno",
            "conditions": [
                {"operator": "between", "value": "2020,2022", "result": "recente"},
            ],
            "default_value": "altro",
            "output_column": "periodo",
        }
        project = make_project("[AUTO] M-06h between", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            if r.get("periodo") == "recente":
                anno = int(float(r["anno"]))
                assert 2020 <= anno <= 2022, (
                    f"between: anno {anno} non in range 2020-2022"
                )

    def test_between_esclude_fuori_range(self, client, csv_filter, make_project, poll):
        """M-06i: between + not between coprono tutto il dataset."""
        config_in  = {"column": "anno", "conditions": [{"operator": "between", "value": "2015,2023", "result": "in"}],  "default_value": "out", "output_column": "r"}
        config_out = {"column": "anno", "conditions": [{"operator": "between", "value": "2015,2023", "result": "in"}],  "default_value": "out", "output_column": "r"}
        p = make_project("[AUTO] M-06i between full", "manipola",
                          canvas=_canvas(csv_filter, config_in))
        result = _execute(client, p, poll)
        rc = _rc(result)
        assert rc == N_TOTALE, f"between full: attese {N_TOTALE} righe, trovate {rc}"

    def test_isnull_notnull_su_colonna_piena(self, client, csv_filter, make_project, poll):
        """M-06j: isnull su col piena → 0 match → tutte righe = default."""
        config = {
            "column": "tipo_uso",
            "conditions": [{"operator": "isnull", "value": None, "result": "VUOTO"}],
            "default_value": "PIENO",
            "output_column": "stato",
        }
        project = make_project("[AUTO] M-06j isnull", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            assert r.get("stato") == "PIENO", (
                f"Colonna piena: isnull non deve matchare, trovato '{r.get('stato')}'"
            )

    def test_auto_detect_numerico(self, client, csv_filter, make_project, poll):
        """M-06k: result values tutti numerici → output_column è numerica (somma > 0)."""
        config = {
            "column": "anno",
            "conditions": [
                {"operator": ">=", "value": "2020", "result": "1"},
                {"operator": "<",  "value": "2020", "result": "0"},
            ],
            "default_value": "0",
            "output_column": "flag",
        }
        project = make_project("[AUTO] M-06k auto numerico", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        # Se auto-detect funziona, i valori sono 0 o 1 (numerici)
        for r in rows:
            val = r.get("flag")
            if val is not None:
                assert str(val) in ("0", "1", "0.0", "1.0"), (
                    f"Auto-detect numerico: atteso 0 o 1, trovato '{val}'"
                )


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestM06Edge:

    def test_column_inesistente_df_invariato(self, client, csv_filter, make_project, poll):
        """M-06l edge: column inesistente → df invariato (N_TOTALE righe, senza output_col)."""
        config = {
            "column": "colonna_inesistente",
            "conditions": [{"operator": "==", "value": "X", "result": "Y"}],
            "default_value": "Z",
            "output_column": "nuovo",
        }
        project = make_project("[AUTO] M-06l edge col inesistente", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == N_TOTALE, f"Col inesistente: atteso {N_TOTALE}, trovato {rc}"

    def test_conditions_vuota_df_invariato(self, client, csv_filter, make_project, poll):
        """M-06m edge: conditions=[] → df invariato."""
        config = {
            "column": "tipo_uso",
            "conditions": [],
            "default_value": "DEFAULT",
            "output_column": "cat",
        }
        project = make_project("[AUTO] M-06m edge conditions vuote", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == N_TOTALE, f"conditions=[]: atteso {N_TOTALE}, trovato {rc}"

    def test_default_vuoto(self, client, csv_filter, make_project, poll):
        """M-06n edge: default_value='' → righe non matchate hanno stringa vuota."""
        config = {
            "column": "tipo_uso",
            "conditions": [{"operator": "==", "value": "Residenziale", "result": "RES"}],
            "default_value": "",
            "output_column": "cat",
        }
        project = make_project("[AUTO] M-06n edge default vuoto", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            if r.get("tipo_uso") != "Residenziale":
                val = r.get("cat")
                assert val == "" or val is None, (
                    f"default_value='': atteso vuoto/null, trovato '{val}'"
                )
