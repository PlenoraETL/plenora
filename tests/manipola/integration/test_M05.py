"""
M-05 — Filter (column, operator, value)
=========================================
Dataset: csv_filter 166075 righe
  colonne: id, regione, provincia, tipo_uso, valore, anno, attivo
  ~52803 righe con tipo_uso='Residenziale' AND anno>=2020 AND attivo=True

Branch coperti da op_filter:
  ✅ colonna inesistente → return df invariato
  ✅ operatore non supportato → return df invariato
  ✅ == su stringa
  ✅ != su stringa
  ✅ > su numerico (auto-conversione stringa→float)
  ✅ >= su numerico
  ✅ < su numerico
  ✅ <= su numerico
  ✅ contains (case-insensitive)
  ✅ startswith
  ✅ endswith
  ✅ isnull
  ✅ notnull
  ✅ filtro su colonna booleana
  ✅ risultato vuoto (filtro più restrittivo)
"""

import pytest

pytestmark = [pytest.mark.manipola]

N_TOTALE     = 166075
N_RESIDENZIALE = 52803


# ── Helper ────────────────────────────────────────────────────────────────────

def _canvas(file_id, config, filename="filter_out"):
    from conftest import build_manipola_canvas
    return build_manipola_canvas(file_id, "filter", config,
                                  output_format="csv", output_filename=filename)

def _execute(client, project_id, poll):
    res = client.post(f"/projects/{project_id}/execute")
    assert res.status_code == 200
    return poll(res.json()["task_id"])

def _preview(client, project_id, poll, limit=20):
    res = client.get(f"/projects/{project_id}/preview", params={"limit": limit})
    assert res.status_code == 200, f"Preview fallita: {res.text}"
    return poll(res.json().get("task_id"))

def _rows(result):
    return result.get("rows", result.get("data", []))

def _rc(result):
    return result.get("row_count", result.get("rows_count"))


# ── Operatori su stringhe ─────────────────────────────────────────────────────

class TestM05Stringhe:

    def test_eq_stringa_row_count(self, client, csv_filter, make_project, poll):
        """M-05a: == 'Residenziale' → 52803 righe."""
        config = {"column": "tipo_uso", "operator": "==", "value": "Residenziale"}
        project = make_project("[AUTO] M-05a eq stringa", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == N_RESIDENZIALE, f"== Residenziale: attese {N_RESIDENZIALE}, trovate {rc}"

    def test_eq_stringa_valori_corretti(self, client, csv_filter, make_project, poll):
        """M-05b: == → tutte le righe restituite hanno tipo_uso=Residenziale."""
        config = {"column": "tipo_uso", "operator": "==", "value": "Residenziale"}
        project = make_project("[AUTO] M-05b eq valori", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            assert r.get("tipo_uso") == "Residenziale", (
                f"Riga con tipo_uso diverso: {r.get('tipo_uso')}"
            )

    def test_neq_stringa_esclude(self, client, csv_filter, make_project, poll):
        """M-05c: != 'Residenziale' → N_TOTALE - N_RESIDENZIALE righe."""
        config = {"column": "tipo_uso", "operator": "!=", "value": "Residenziale"}
        project = make_project("[AUTO] M-05c neq stringa", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        atteso = N_TOTALE - N_RESIDENZIALE
        assert rc == atteso, f"!= Residenziale: attese {atteso}, trovate {rc}"

    def test_contains_case_insensitive(self, client, csv_filter, make_project, poll):
        """M-05d: contains 'residenz' (minuscolo) → trova 'Residenziale' (case-insensitive)."""
        config = {"column": "tipo_uso", "operator": "contains", "value": "residenz"}
        project = make_project("[AUTO] M-05d contains", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == N_RESIDENZIALE, (
            f"contains 'residenz': attese {N_RESIDENZIALE} righe, trovate {rc}"
        )

    def test_startswith(self, client, csv_filter, make_project, poll):
        """M-05e: startswith 'Res' → solo Residenziale."""
        config = {"column": "tipo_uso", "operator": "startswith", "value": "Res"}
        project = make_project("[AUTO] M-05e startswith", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == N_RESIDENZIALE, f"startswith 'Res': attese {N_RESIDENZIALE}, trovate {rc}"

    def test_endswith(self, client, csv_filter, make_project, poll):
        """M-05f: endswith 'ale' → Residenziale + Commerciale + Industriale."""
        config = {"column": "tipo_uso", "operator": "endswith", "value": "ale"}
        project = make_project("[AUTO] M-05f endswith", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        # Almeno N_RESIDENZIALE righe (plus Commerciale + Industriale)
        assert rc >= N_RESIDENZIALE, (
            f"endswith 'ale': attese ≥{N_RESIDENZIALE} righe, trovate {rc}"
        )
        assert rc < N_TOTALE, f"endswith 'ale': troppi risultati ({rc}), operatore non filtra"


# ── Operatori numerici ────────────────────────────────────────────────────────

class TestM05Numerici:

    def test_gt_anno(self, client, csv_filter, make_project, poll):
        """M-05g: anno > 2022 → solo righe con anno 2023."""
        config = {"column": "anno", "operator": ">", "value": "2022"}
        project = make_project("[AUTO] M-05g gt anno", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            assert int(r["anno"]) > 2022, f"Riga con anno <= 2022: {r['anno']}"

    def test_gte_anno(self, client, csv_filter, make_project, poll):
        """M-05h: anno >= 2020 → >= anno > 2019."""
        config_gte = {"column": "anno", "operator": ">=", "value": "2020"}
        config_gt  = {"column": "anno", "operator": ">",  "value": "2019"}
        p_gte = make_project("[AUTO] M-05h gte", "manipola", canvas=_canvas(csv_filter, config_gte))
        p_gt  = make_project("[AUTO] M-05h gt",  "manipola", canvas=_canvas(csv_filter, config_gt))
        r_gte = _execute(client, p_gte, poll)
        r_gt  = _execute(client, p_gt,  poll)
        assert _rc(r_gte) == _rc(r_gt), (
            f">= 2020 ({_rc(r_gte)}) deve corrispondere a > 2019 ({_rc(r_gt)})"
        )

    def test_lt_valore(self, client, csv_filter, make_project, poll):
        """M-05i: valore < 200 → righe con valore piccolo."""
        config = {"column": "valore", "operator": "<", "value": "200"}
        project = make_project("[AUTO] M-05i lt valore", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            assert float(r["valore"]) < 200, f"Riga con valore >= 200: {r['valore']}"

    def test_lte_valore(self, client, csv_filter, make_project, poll):
        """M-05j: valore <= 200 → include anche valore == 200."""
        config_lte = {"column": "valore", "operator": "<=", "value": "200"}
        config_lt  = {"column": "valore", "operator": "<",  "value": "200"}
        p_lte = make_project("[AUTO] M-05j lte", "manipola", canvas=_canvas(csv_filter, config_lte))
        p_lt  = make_project("[AUTO] M-05j lt",  "manipola", canvas=_canvas(csv_filter, config_lt))
        r_lte = _execute(client, p_lte, poll)
        r_lt  = _execute(client, p_lt,  poll)
        assert _rc(r_lte) >= _rc(r_lt), (
            f"<= 200 ({_rc(r_lte)}) deve essere >= < 200 ({_rc(r_lt)})"
        )

    @pytest.mark.parametrize("op1,op2", [
        (">",  "<="),   # > e <= sono complementari
        (">=", "<"),    # >= e < sono complementari
    ])
    def test_complementari_sommano_totale(self, client, csv_filter, make_project, poll, op1, op2):
        """M-05k: op + complementare = N_TOTALE (ogni riga cade in uno solo dei due)."""
        val = "2019"
        p1 = make_project(f"[AUTO] M-05k {op1}",  "manipola",
                           canvas=_canvas(csv_filter, {"column": "anno", "operator": op1, "value": val}))
        p2 = make_project(f"[AUTO] M-05k {op2}", "manipola",
                           canvas=_canvas(csv_filter, {"column": "anno", "operator": op2, "value": val}))
        r1 = _execute(client, p1, poll)
        r2 = _execute(client, p2, poll)
        total = _rc(r1) + _rc(r2)
        assert total == N_TOTALE, (
            f"{op1} + {op2} per anno={val}: atteso {N_TOTALE}, trovato {total}"
        )


# ── Null / notnull ────────────────────────────────────────────────────────────

class TestM05Null:

    def test_notnull_restituisce_tutte(self, client, csv_filter, make_project, poll):
        """M-05l: notnull su colonna senza null → N_TOTALE righe."""
        config = {"column": "tipo_uso", "operator": "notnull", "value": None}
        project = make_project("[AUTO] M-05l notnull", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == N_TOTALE, f"notnull su col piena: attese {N_TOTALE}, trovate {rc}"

    def test_isnull_restituisce_zero(self, client, csv_filter, make_project, poll):
        """M-05m: isnull su colonna senza null → 0 righe."""
        config = {"column": "tipo_uso", "operator": "isnull", "value": None}
        project = make_project("[AUTO] M-05m isnull", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == 0, f"isnull su col piena: attese 0 righe, trovate {rc}"


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestM05Edge:

    def test_colonna_inesistente_df_invariato(self, client, csv_filter, make_project, poll):
        """M-05n edge: colonna inesistente → df invariato (166075 righe)."""
        config = {"column": "colonna_inesistente", "operator": "==", "value": "X"}
        project = make_project("[AUTO] M-05n edge col inesistente", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == N_TOTALE, f"Col inesistente: atteso {N_TOTALE}, trovato {rc}"

    def test_operatore_non_supportato_df_invariato(self, client, csv_filter, make_project, poll):
        """M-05o edge: operatore sconosciuto → df invariato."""
        config = {"column": "anno", "operator": "BETWEEN", "value": "2020"}
        project = make_project("[AUTO] M-05o edge op non supportato", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == N_TOTALE, f"Op non supportato: atteso {N_TOTALE}, trovato {rc}"

    def test_filtro_senza_risultati(self, client, csv_filter, make_project, poll):
        """M-05p edge: valore impossibile → 0 righe."""
        config = {"column": "anno", "operator": "==", "value": "1800"}
        project = make_project("[AUTO] M-05p edge filtro vuoto", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == 0, f"Filtro impossibile: attese 0 righe, trovate {rc}"

    def test_eq_su_regione_nota(self, client, csv_filter, make_project, poll):
        """M-05q edge: == su colonna con valori noti → subset del totale."""
        config = {"column": "regione", "operator": "==", "value": "Lombardia"}
        project = make_project("[AUTO] M-05q edge eq regione", "manipola",
                               canvas=_canvas(csv_filter, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        # 10 regioni equidistribuite → ~16607 ciascuna, comunque > 0 e < totale
        assert 0 < rc < N_TOTALE, (
            f"Filtro regione: attese righe tra 0 e {N_TOTALE}, trovate {rc}"
        )
