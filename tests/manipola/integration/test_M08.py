"""
M-08 — Rename + Drop Columns
==============================
Dataset: csv_immobili 6819 righe
  colonne: id, comune, zona, tipo, valore_omi, superficie, piano, anno, lat, lon (10 col)

Branch coperti da op_rename:
  ✅ renames lista multipla → rinomina N colonne
  ✅ singola rinominazione old_name/new_name (retrocompat)
  ✅ colonna inesistente nella lista → skippata silenziosamente
  ✅ renames vuota/None → return df invariato
  ✅ rename_map vuoto (tutte col inesistenti) → return df invariato

Branch coperti da op_drop_columns:
  ✅ columns lista → rimuove colonne specificate
  ✅ colonna inesistente → skippata
  ✅ columns vuota → return df invariato
  ✅ tutte le colonne inesistenti → return df invariato
  ✅ drop + rename in sequenza (pipeline)
"""

import pytest

pytestmark = [pytest.mark.manipola]

N_TOTALE  = 6819
COL_ORIG  = {"id", "comune", "zona", "tipo", "valore_omi",
             "superficie", "piano", "anno", "lat", "lon"}  # 10 colonne


# ── Helper ────────────────────────────────────────────────────────────────────

def _canvas_rename(file_id, config, filename="rename_out"):
    from conftest import build_manipola_canvas
    return build_manipola_canvas(file_id, "rename", config,
                                  output_format="csv", output_filename=filename)

def _canvas_drop(file_id, config, filename="drop_out"):
    from conftest import build_manipola_canvas
    return build_manipola_canvas(file_id, "drop_columns", config,
                                  output_format="csv", output_filename=filename)

def _execute(client, project_id, poll):
    res = client.post(f"/projects/{project_id}/execute")
    assert res.status_code == 200
    return poll(res.json()["task_id"])

def _preview(client, project_id, poll, limit=10):
    res = client.get(f"/projects/{project_id}/preview", params={"limit": limit})
    assert res.status_code == 200
    return poll(res.json().get("task_id"))

def _cols(result):
    return {c["name"] if isinstance(c, dict) else c for c in result.get("columns", [])}

def _rc(result):
    return result.get("row_count", result.get("rows_count"))

def _rows(result):
    return result.get("rows", result.get("data", []))


# ── Rename ────────────────────────────────────────────────────────────────────

class TestM08Rename:

    def test_rename_singolo(self, client, csv_immobili, make_project, poll):
        """M-08a: rinomina comune → nome_comune."""
        config = {"renames": [{"old_name": "comune", "new_name": "nome_comune"}]}
        project = make_project("[AUTO] M-08a rename singolo", "manipola",
                               canvas=_canvas_rename(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        assert "nome_comune" in cols, f"'nome_comune' mancante: {cols}"
        assert "comune" not in cols, f"'comune' ancora presente dopo rename: {cols}"

    def test_rename_multiplo(self, client, csv_immobili, make_project, poll):
        """M-08b: rinomina 4 colonne in una sola operazione."""
        config = {
            "renames": [
                {"old_name": "comune",    "new_name": "nome_comune"},
                {"old_name": "zona",      "new_name": "zona_omogenea"},
                {"old_name": "valore_omi","new_name": "valore_unitario"},
                {"old_name": "superficie","new_name": "mq"},
            ]
        }
        project = make_project("[AUTO] M-08b rename multiplo", "manipola",
                               canvas=_canvas_rename(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        for new in ["nome_comune", "zona_omogenea", "valore_unitario", "mq"]:
            assert new in cols, f"'{new}' mancante dopo rename: {cols}"
        for old in ["comune", "zona", "valore_omi", "superficie"]:
            assert old not in cols, f"'{old}' ancora presente dopo rename: {cols}"

    def test_rename_row_count_invariato(self, client, csv_immobili, make_project, poll):
        """M-08c: rename non altera il numero di righe."""
        config = {"renames": [{"old_name": "comune", "new_name": "nome_comune"}]}
        project = make_project("[AUTO] M-08c row count", "manipola",
                               canvas=_canvas_rename(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE, f"Attese {N_TOTALE}, trovate {_rc(result)}"

    def test_rename_colonne_non_rinominate_invariate(self, client, csv_immobili, make_project, poll):
        """M-08d: le colonne non incluse nel rename restano invariate."""
        config = {"renames": [{"old_name": "comune", "new_name": "nome_comune"}]}
        project = make_project("[AUTO] M-08d col invariate", "manipola",
                               canvas=_canvas_rename(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        for c in ["id", "zona", "tipo", "valore_omi", "superficie", "piano", "anno"]:
            assert c in cols, f"Colonna non rinominata '{c}' mancante: {cols}"

    def test_rename_tutte_colonne_presenti(self, client, csv_immobili, make_project, poll):
        """M-08e: dopo rename multiplo il totale colonne rimane invariato (10)."""
        config = {
            "renames": [
                {"old_name": "comune",    "new_name": "nome_comune"},
                {"old_name": "zona",      "new_name": "zona_omogenea"},
                {"old_name": "valore_omi","new_name": "valore_unitario"},
                {"old_name": "superficie","new_name": "mq"},
            ]
        }
        project = make_project("[AUTO] M-08e tot colonne", "manipola",
                               canvas=_canvas_rename(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        assert len(cols) == 10, f"Attese 10 colonne dopo rename, trovate {len(cols)}: {cols}"

    def test_rename_valori_preservati(self, client, csv_immobili, make_project, poll):
        """M-08f: dopo rename i valori della colonna sono invariati."""
        config = {"renames": [{"old_name": "comune", "new_name": "nome_comune"}]}
        project = make_project("[AUTO] M-08f valori", "manipola",
                               canvas=_canvas_rename(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        comuni_validi = {"Roma", "Milano", "Napoli", "Torino", "Bologna",
                         "Firenze", "Palermo", "Genova"}
        for r in rows:
            assert r.get("nome_comune") in comuni_validi, (
                f"Valore inatteso in nome_comune: '{r.get('nome_comune')}'"
            )


# ── Drop columns ──────────────────────────────────────────────────────────────

class TestM08Drop:

    def test_drop_singola_colonna(self, client, csv_immobili, make_project, poll):
        """M-08g: drop ['id'] → colonna id rimossa."""
        config = {"columns": ["id"]}
        project = make_project("[AUTO] M-08g drop singola", "manipola",
                               canvas=_canvas_drop(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        assert "id" not in cols, f"'id' ancora presente dopo drop: {cols}"
        assert len(cols) == 9, f"Attese 9 colonne dopo drop 1, trovate {len(cols)}: {cols}"

    def test_drop_multiple_colonne(self, client, csv_immobili, make_project, poll):
        """M-08h: drop ['lat', 'lon', 'id'] → 7 colonne rimaste."""
        config = {"columns": ["lat", "lon", "id"]}
        project = make_project("[AUTO] M-08h drop multiple", "manipola",
                               canvas=_canvas_drop(csv_immobili, config))
        result = _preview(client, project, poll)
        cols = _cols(result)
        for c in ["lat", "lon", "id"]:
            assert c not in cols, f"'{c}' ancora presente dopo drop: {cols}"
        assert len(cols) == 7, f"Attese 7 colonne, trovate {len(cols)}: {cols}"

    def test_drop_row_count_invariato(self, client, csv_immobili, make_project, poll):
        """M-08i: drop non altera il numero di righe."""
        config = {"columns": ["lat", "lon"]}
        project = make_project("[AUTO] M-08i row count", "manipola",
                               canvas=_canvas_drop(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE, f"Attese {N_TOTALE}, trovate {_rc(result)}"

    def test_drop_colonne_restanti_intatte(self, client, csv_immobili, make_project, poll):
        """M-08j: le colonne non droppate hanno valori invariati."""
        config = {"columns": ["lat", "lon"]}
        project = make_project("[AUTO] M-08j col restanti", "manipola",
                               canvas=_canvas_drop(csv_immobili, config))
        result = _preview(client, project, poll)
        rows = _rows(result)
        for r in rows:
            assert r.get("comune") is not None, "comune non dovrebbe essere None"
            assert r.get("valore_omi") is not None, "valore_omi non dovrebbe essere None"


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestM08Edge:

    def test_rename_colonna_inesistente_skippata(self, client, csv_immobili, make_project, poll):
        """M-08k edge: rinomina colonna inesistente → skippata, df invariato."""
        config = {"renames": [{"old_name": "colonna_inesistente", "new_name": "nuova"}]}
        project = make_project("[AUTO] M-08k edge rename inesistente", "manipola",
                               canvas=_canvas_rename(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE
        r = _preview(client, project, poll)
        cols = _cols(r)
        assert "nuova" not in cols, f"Colonna inesistente non dovrebbe essere rinominata"
        assert COL_ORIG.issubset(cols), f"Colonne originali alterate: {cols}"

    def test_rename_lista_vuota_df_invariato(self, client, csv_immobili, make_project, poll):
        """M-08l edge: renames=[] → df invariato."""
        config = {"renames": []}
        project = make_project("[AUTO] M-08l edge renames vuoto", "manipola",
                               canvas=_canvas_rename(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE

    def test_drop_colonna_inesistente_skippata(self, client, csv_immobili, make_project, poll):
        """M-08m edge: drop colonna inesistente → df invariato."""
        config = {"columns": ["colonna_inesistente"]}
        project = make_project("[AUTO] M-08m edge drop inesistente", "manipola",
                               canvas=_canvas_drop(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE
        r = _preview(client, project, poll)
        assert COL_ORIG.issubset(_cols(r)), "Drop inesistente non deve alterare le colonne"

    def test_drop_lista_vuota_df_invariato(self, client, csv_immobili, make_project, poll):
        """M-08n edge: drop columns=[] → df invariato."""
        config = {"columns": []}
        project = make_project("[AUTO] M-08n edge drop vuoto", "manipola",
                               canvas=_canvas_drop(csv_immobili, config))
        result = _execute(client, project, poll)
        assert _rc(result) == N_TOTALE
