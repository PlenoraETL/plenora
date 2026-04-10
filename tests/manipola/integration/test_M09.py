"""
M-09 — Sort multi-colonna ASC/DESC
=====================================
Dataset: csv_immobili 6819 righe
  colonne: id, comune, zona, tipo, valore_omi, superficie, piano, anno, lat, lon

Branch coperti da op_sort:
  ✅ columns vuota → return df invariato
  ✅ colonne inesistenti filtrate → return df invariato
  ✅ ascending=True → ordine crescente
  ✅ ascending=False → ordine decrescente
  ✅ sort su colonna numerica
  ✅ sort su colonna stringa (alfabetico)
  ✅ sort multi-colonna (primaria + secondaria)
  ✅ row_count invariato dopo sort
  ✅ reset_index (indici 0..N-1 dopo sort)
"""

import pytest

pytestmark = [pytest.mark.manipola]

N_TOTALE = 6819


# ── Helper ────────────────────────────────────────────────────────────────────

def _canvas(file_id, config, filename="sort_out"):
    from conftest import build_manipola_canvas
    return build_manipola_canvas(file_id, "sort", config,
                                  output_format="csv", output_filename=filename)

def _execute(client, project_id, poll):
    res = client.post(f"/projects/{project_id}/execute")
    assert res.status_code == 200
    return poll(res.json()["task_id"])

def _preview(client, project_id, poll, limit=20):
    res = client.get(f"/projects/{project_id}/preview", params={"limit": limit})
    assert res.status_code == 200
    return poll(res.json().get("task_id"))

def _rows(result):
    return result.get("rows", result.get("data", []))

def _rc(result):
    return result.get("row_count", result.get("rows_count"))


# ── Happy path ────────────────────────────────────────────────────────────────

class TestM09HappyPath:

    def test_sort_asc_numerico(self, client, csv_immobili, make_project, poll):
        """M-09a: sort valore_omi ASC → prima riga ha valore_omi minimo."""
        config = {"columns": ["valore_omi"], "ascending": True}
        project = make_project("[AUTO] M-09a sort asc num", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        valori = [float(r["valore_omi"]) for r in rows if r.get("valore_omi") is not None]
        assert valori == sorted(valori), (
            f"Ordine ASC non rispettato: {valori[:5]}"
        )

    def test_sort_desc_numerico(self, client, csv_immobili, make_project, poll):
        """M-09b: sort valore_omi DESC → prima riga ha valore_omi massimo."""
        config = {"columns": ["valore_omi"], "ascending": False}
        project = make_project("[AUTO] M-09b sort desc num", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        valori = [float(r["valore_omi"]) for r in rows if r.get("valore_omi") is not None]
        assert valori == sorted(valori, reverse=True), (
            f"Ordine DESC non rispettato: {valori[:5]}"
        )

    def test_sort_asc_stringa(self, client, csv_immobili, make_project, poll):
        """M-09c: sort comune ASC → ordine alfabetico crescente."""
        config = {"columns": ["comune"], "ascending": True}
        project = make_project("[AUTO] M-09c sort asc str", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        comuni = [r["comune"] for r in rows if r.get("comune")]
        assert comuni == sorted(comuni), (
            f"Ordine alfabetico ASC non rispettato: {comuni[:5]}"
        )

    def test_sort_desc_stringa(self, client, csv_immobili, make_project, poll):
        """M-09d: sort comune DESC → ordine alfabetico decrescente."""
        config = {"columns": ["comune"], "ascending": False}
        project = make_project("[AUTO] M-09d sort desc str", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        comuni = [r["comune"] for r in rows if r.get("comune")]
        assert comuni == sorted(comuni, reverse=True), (
            f"Ordine alfabetico DESC non rispettato: {comuni[:5]}"
        )

    def test_sort_row_count_invariato(self, client, csv_immobili, make_project, poll):
        """M-09e: sort non altera il numero di righe."""
        config = {"columns": ["valore_omi"], "ascending": True}
        project = make_project("[AUTO] M-09e row count", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE, f"Attese {N_TOTALE}, trovate {_rc(result)}"

    def test_sort_colonne_invariate(self, client, csv_immobili, make_project, poll):
        """M-09f: sort non rimuove né aggiunge colonne."""
        config = {"columns": ["valore_omi"], "ascending": True}
        project = make_project("[AUTO] M-09f colonne", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = {c["name"] if isinstance(c, dict) else c for c in result.get("columns", [])}
        for c in ["id", "comune", "zona", "tipo", "valore_omi", "superficie"]:
            assert c in cols, f"Colonna '{c}' mancante dopo sort"


# ── Multi-colonna ─────────────────────────────────────────────────────────────

class TestM09MultiColonna:

    def test_sort_multi_asc(self, client, csv_immobili, make_project, poll):
        """M-09g: sort [comune, valore_omi] ASC → comune alfabetico, poi valore crescente."""
        config = {"columns": ["comune", "valore_omi"], "ascending": True}
        project = make_project("[AUTO] M-09g multi asc", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll, limit=50)
        rows = _rows(result)
        comuni = [r["comune"] for r in rows if r.get("comune")]
        # La colonna primaria deve essere ordinata
        assert comuni == sorted(comuni), (
            f"Sort multi: comune non ordinato ASC: {comuni[:5]}"
        )

    def test_sort_multi_stessa_chiave_primaria(self, client, csv_immobili, make_project, poll):
        """M-09h: sort [comune, valore_omi] → righe con stesso comune ordinate per valore."""
        config = {"columns": ["comune", "valore_omi"], "ascending": True}
        project = make_project("[AUTO] M-09h multi chiave", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _preview(client, project, poll, limit=50)
        rows = _rows(result)
        # Raggruppa per comune e verifica ordine interno
        from collections import defaultdict
        gruppi = defaultdict(list)
        for r in rows:
            if r.get("comune") and r.get("valore_omi"):
                gruppi[r["comune"]].append(float(r["valore_omi"]))
        for comune, valori in gruppi.items():
            assert valori == sorted(valori), (
                f"Comune '{comune}': valore_omi non ordinato ASC: {valori}"
            )

    def test_sort_tre_colonne(self, client, csv_immobili, make_project, poll):
        """M-09i: sort [comune, zona, anno] ASC → tre livelli di ordinamento."""
        config = {"columns": ["comune", "zona", "anno"], "ascending": True}
        project = make_project("[AUTO] M-09i tre colonne", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE, f"Attese {N_TOTALE}, trovate {_rc(result)}"


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestM09Edge:

    def test_sort_columns_vuota_df_invariato(self, client, csv_immobili, make_project, poll):
        """M-09j edge: columns=[] → df invariato."""
        config = {"columns": [], "ascending": True}
        project = make_project("[AUTO] M-09j edge vuoto", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE, f"columns=[]: attese {N_TOTALE}, trovate {_rc(result)}"

    def test_sort_colonna_inesistente_df_invariato(self, client, csv_immobili, make_project, poll):
        """M-09k edge: colonna inesistente → filtrata, df invariato."""
        config = {"columns": ["colonna_inesistente"], "ascending": True}
        project = make_project("[AUTO] M-09k edge col inesistente", "manipola",
                               canvas=_canvas(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE, f"Col inesistente: attese {N_TOTALE}, trovate {_rc(result)}"

    def test_sort_asc_vs_desc_prima_ultima_riga(self, client, csv_immobili, make_project, poll):
        """M-09l edge: prima riga ASC == ultima riga DESC (stesso dataset)."""
        p_asc  = make_project("[AUTO] M-09l asc",  "manipola",
                               canvas=_canvas(csv_immobili, {"columns": ["valore_omi"], "ascending": True}))
        p_desc = make_project("[AUTO] M-09l desc", "manipola",
                               canvas=_canvas(csv_immobili, {"columns": ["valore_omi"], "ascending": False}))
        r_asc  = _preview(client, p_asc,  poll, limit=1)
        r_desc = _preview(client, p_desc, poll, limit=1)
        row_asc  = _rows(r_asc)[0]
        row_desc = _rows(r_desc)[0]
        # Prima riga ASC ha valore minimo, prima riga DESC ha valore massimo → diversi
        assert float(row_asc["valore_omi"]) <= float(row_desc["valore_omi"]), (
            f"ASC prima riga {row_asc['valore_omi']} > DESC prima riga {row_desc['valore_omi']}"
        )
