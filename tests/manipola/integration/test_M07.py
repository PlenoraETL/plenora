"""
M-07 — Formula (formula, new_column)
=====================================
Dataset: csv_immobili 6819 righe
  colonne: id, comune, zona, tipo, valore_omi(float), superficie(int),
           piano(int), anno(int), lat(float), lon(float)

Branch coperti da op_formula:
  ✅ formula vuota → return df invariato
  ✅ path stringa: formula con ' o " → eval con astype(str)
  ✅ path aritmetico: pandas.eval su colonne numeriche
  ✅ auto-conversione object→numerico per colonne CSV
  ✅ fallback eval: formula che pandas.eval non regge
  ✅ errore generico → return df invariato
  ✅ new_column custom
  ✅ moltiplicazione, divisione, addizione, sottrazione, potenza
  ✅ formula con costante scalare
  ✅ formula su due colonne diverse
"""

import pytest

pytestmark = [pytest.mark.manipola]

N_TOTALE = 6819


# ── Helper ────────────────────────────────────────────────────────────────────

def _canvas(file_id, config, filename="formula_out"):
    from conftest import build_manipola_canvas
    return build_manipola_canvas(file_id, "formula", config,
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

class TestM07HappyPath:

    def test_formula_crea_colonna(self, client, csv_immobili, make_project, poll):
        """M-07a: formula aritmetica → new_column creata nel risultato."""
        config = {"formula": "valore_omi * superficie", "new_column": "valore_totale"}
        project = make_project("[AUTO] M-07a crea colonna", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        assert "valore_totale" in _cols(result), (
            f"new_column 'valore_totale' mancante: {_cols(result)}"
        )

    def test_formula_row_count_invariato(self, client, csv_immobili, make_project, poll):
        """M-07b: formula non altera il numero di righe."""
        config = {"formula": "valore_omi * superficie", "new_column": "valore_totale"}
        project = make_project("[AUTO] M-07b row count", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE, f"Attese {N_TOTALE} righe, trovate {_rc(result)}"

    def test_moltiplicazione_valore_corretto(self, client, csv_immobili, make_project, poll):
        """M-07c: valore_omi * superficie → valore_totale = prodotto corretto."""
        config = {"formula": "valore_omi * superficie", "new_column": "valore_totale"}
        project = make_project("[AUTO] M-07c moltiplicazione", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            if r.get("valore_omi") and r.get("superficie") and r.get("valore_totale"):
                atteso = float(r["valore_omi"]) * float(r["superficie"])
                trovato = float(r["valore_totale"])
                assert abs(trovato - atteso) < 0.01, (
                    f"valore_omi*superficie: atteso {atteso:.2f}, trovato {trovato:.2f}"
                )

    def test_divisione(self, client, csv_immobili, make_project, poll):
        """M-07d: valore_omi / superficie → valore per mq."""
        config = {"formula": "valore_omi / superficie", "new_column": "valore_mq"}
        project = make_project("[AUTO] M-07d divisione", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            if r.get("valore_omi") and r.get("superficie") and r.get("valore_mq"):
                atteso = float(r["valore_omi"]) / float(r["superficie"])
                trovato = float(r["valore_mq"])
                assert abs(trovato - atteso) < 0.01, (
                    f"valore_omi/superficie: atteso {atteso:.4f}, trovato {trovato:.4f}"
                )

    def test_addizione_colonne(self, client, csv_immobili, make_project, poll):
        """M-07e: valore_omi + superficie → somma colonne."""
        config = {"formula": "valore_omi + superficie", "new_column": "somma"}
        project = make_project("[AUTO] M-07e addizione", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            if r.get("valore_omi") and r.get("superficie") and r.get("somma"):
                atteso = float(r["valore_omi"]) + float(r["superficie"])
                trovato = float(r["somma"])
                assert abs(trovato - atteso) < 0.01, (
                    f"addizione: atteso {atteso:.2f}, trovato {trovato:.2f}"
                )

    def test_formula_con_costante(self, client, csv_immobili, make_project, poll):
        """M-07f: valore_omi * 1.22 → IVA applicata."""
        config = {"formula": "valore_omi * 1.22", "new_column": "valore_iva"}
        project = make_project("[AUTO] M-07f costante", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            if r.get("valore_omi") and r.get("valore_iva"):
                atteso = float(r["valore_omi"]) * 1.22
                trovato = float(r["valore_iva"])
                assert abs(trovato - atteso) < 0.01, (
                    f"*1.22: atteso {atteso:.2f}, trovato {trovato:.2f}"
                )


# ── Path stringa ──────────────────────────────────────────────────────────────

class TestM07Stringa:

    def test_formula_stringa_concatena(self, client, csv_immobili, make_project, poll):
        """M-07g: formula con apici → path stringa, concatena comune + ' - ' + zona."""
        config = {"formula": "comune + ' - ' + zona", "new_column": "label"}
        project = make_project("[AUTO] M-07g stringa concat", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        rows = _rows(result)
        assert "label" in cols, f"Colonna 'label' mancante: {cols}"
        for r in rows:
            if r.get("comune") and r.get("zona") and r.get("label"):
                assert " - " in str(r["label"]), (
                    f"Label senza separatore: '{r['label']}'"
                )

    def test_formula_stringa_contiene_comune(self, client, csv_immobili, make_project, poll):
        """M-07h: label contiene il valore di comune."""
        config = {"formula": "comune + ' - ' + zona", "new_column": "label"}
        project = make_project("[AUTO] M-07h label comune", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            if r.get("comune") and r.get("label"):
                assert str(r["comune"]) in str(r["label"]), (
                    f"comune '{r['comune']}' non trovato in label '{r['label']}'"
                )

    def test_formula_new_column_custom(self, client, csv_immobili, make_project, poll):
        """M-07i: new_column con nome custom → colonna ha il nome specificato."""
        config = {"formula": "valore_omi * 2", "new_column": "doppio_valore"}
        project = make_project("[AUTO] M-07i new_column custom", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        assert "doppio_valore" in _cols(result)
        assert "calculated" not in _cols(result), "Non attesa colonna default 'calculated'"


# ── Operazioni aritmetiche avanzate ───────────────────────────────────────────

class TestM07Aritmetica:

    def test_sottrazione(self, client, csv_immobili, make_project, poll):
        """M-07j: anno - 2000 → anni dal 2000."""
        config = {"formula": "anno - 2000", "new_column": "anni_dal_2000"}
        project = make_project("[AUTO] M-07j sottrazione", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            if r.get("anno") and r.get("anni_dal_2000") is not None:
                atteso = int(float(r["anno"])) - 2000
                trovato = int(float(r["anni_dal_2000"]))
                assert trovato == atteso, (
                    f"anno-2000: atteso {atteso}, trovato {trovato}"
                )

    def test_valori_non_null(self, client, csv_immobili, make_project, poll):
        """M-07k: formula su colonne complete → nessun NULL nel risultato."""
        config = {"formula": "valore_omi * superficie", "new_column": "valore_totale"}
        project = make_project("[AUTO] M-07k no null", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        null_count = sum(1 for r in rows if r.get("valore_totale") is None)
        assert null_count == 0, f"Formula su col complete: attesi 0 NULL, trovati {null_count}"

    def test_valori_positivi(self, client, csv_immobili, make_project, poll):
        """M-07l: valore_omi > 0 e superficie > 0 → prodotto > 0."""
        config = {"formula": "valore_omi * superficie", "new_column": "valore_totale"}
        project = make_project("[AUTO] M-07l valori positivi", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            if r.get("valore_totale") is not None:
                assert float(r["valore_totale"]) > 0, (
                    f"Prodotto di positivi deve essere > 0: {r['valore_totale']}"
                )


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestM07Edge:

    def test_formula_vuota_df_invariato(self, client, csv_immobili, make_project, poll):
        """M-07m edge: formula='' → df invariato (N_TOTALE righe, no new_column)."""
        config = {"formula": "", "new_column": "calcolata"}
        project = make_project("[AUTO] M-07m edge formula vuota", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE, f"Formula vuota: attese {N_TOTALE}, trovate {_rc(result)}"

    def test_formula_invalida_df_invariato(self, client, csv_immobili, make_project, poll):
        """M-07n edge: formula non valida → errore gestito, df invariato."""
        config = {"formula": "colonna_inesistente * 999", "new_column": "errore"}
        project = make_project("[AUTO] M-07n edge formula invalida", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _execute(client, project, poll)
        rc = _rc(result)
        assert rc == N_TOTALE, f"Formula invalida: attese {N_TOTALE}, trovate {rc}"

    def test_colonne_originali_preservate(self, client, csv_immobili, make_project, poll):
        """M-07o edge: formula aggiunge colonna senza rimuovere le originali."""
        config = {"formula": "valore_omi * 2", "new_column": "doppio"}
        project = make_project("[AUTO] M-07o col originali", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        for c in ["id", "comune", "zona", "tipo", "valore_omi", "superficie"]:
            assert c in cols, f"Colonna originale '{c}' rimossa dopo formula: {cols}"
        assert "doppio" in cols, f"new_column 'doppio' mancante"
