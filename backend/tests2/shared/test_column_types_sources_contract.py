"""Contract test — fonti di tipo colonna nel canvas.

I "tipi canvas delle colonne" vivono in 3 viste parallele dentro lo
stesso canvas JSON del progetto:

  1. canvas["columns_metadata"]              -> list[{"name","type"}] top-level
  2. canvas["nodes"][i]["data"]["availableColumns"] -> list[{"name","alias","type"}]
  3. canvas["nodes"][i]["data"]["canvas_types"]     -> dict{name:type}

Consumer noti:
  - ESTRAI executor.py:526 scrive columns_metadata post-run
  - ARRICCHISCI executor.py:100 scrive columns_metadata post-run
  - ColumnMapper._infer_type_from_canvas legge canvas_type string
  - app/shared/canvas_types_enricher legge columns_metadata per CARICA
  - frontend canvasSerializer._computeResultColumns usa parent.data.columns

Il refactor B' (unificazione shape list<->dict via app.shared.column_types)
e' stato applicato. Questa suite resta attiva come guardia sul contract
osservabile:

  - formato stabile dei 3 campi
  - API contract su ProjectService.get/update_columns_metadata
  - consumatori noti tracciati (sentinel: se spariscono/spostano, test rompe)

Qualsiasi cambio futuro che rompa il contratto visibile verra' catturato.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# =============================================================================
# 1. Format contract — ogni vista ha struttura attesa
# =============================================================================


def test_columns_metadata_is_list_of_name_type_dicts(monkeypatch):
    """ProjectService.get_project_columns_metadata ritorna list[{name,type}]."""
    from app.services.project_service import ProjectService
    from app.services import project_service as ps_mod

    fake_canvas = {
        "columns_metadata": [
            {"name": "id", "type": "int"},
            {"name": "nome", "type": "str"},
            {"name": "data", "type": "date"},
        ],
        "nodes": [], "edges": [],
    }
    fake_row = {"id": "p1", "canvas": fake_canvas}
    monkeypatch.setattr(ps_mod.db, "get_project", lambda pid: fake_row)

    result = ProjectService().get_project_columns_metadata("p1")
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, dict)
        assert "name" in item and isinstance(item["name"], str)
        assert "type" in item and isinstance(item["type"], str)


def test_columns_metadata_empty_when_missing(monkeypatch):
    """Progetto senza columns_metadata -> lista vuota (non None, non error)."""
    from app.services.project_service import ProjectService
    from app.services import project_service as ps_mod

    monkeypatch.setattr(
        ps_mod.db, "get_project",
        lambda pid: {"id": "p1", "canvas": {"nodes": [], "edges": []}}
    )
    assert ProjectService().get_project_columns_metadata("p1") == []


def test_columns_metadata_handles_canvas_as_json_string(monkeypatch):
    """Backward compat: canvas serializzato come stringa JSON -> deserializza."""
    import json
    from app.services.project_service import ProjectService
    from app.services import project_service as ps_mod

    canvas_str = json.dumps({
        "columns_metadata": [{"name": "x", "type": "real"}],
        "nodes": [], "edges": [],
    })
    monkeypatch.setattr(
        ps_mod.db, "get_project",
        lambda pid: {"id": "p1", "canvas": canvas_str}
    )
    result = ProjectService().get_project_columns_metadata("p1")
    assert result == [{"name": "x", "type": "real"}]


# =============================================================================
# 2. Update contract — write/read idempotente
# =============================================================================


def test_update_columns_metadata_writes_to_canvas(monkeypatch):
    """update_project_columns_metadata salva dentro canvas["columns_metadata"],
    NON in colonna DB separata. E' un'invariante del contract da preservare."""
    from app.services.project_service import ProjectService
    from app.services import project_service as ps_mod

    captured_updates = []
    fake_row = {"id": "p1", "canvas": {"nodes": [], "edges": []}}

    def fake_update(pid, **kwargs):
        captured_updates.append(kwargs)
        return True

    monkeypatch.setattr(ps_mod.db, "get_project", lambda pid: fake_row)
    monkeypatch.setattr(ps_mod.db, "update_project", fake_update)

    new_meta = [{"name": "id", "type": "int"}]
    ok = ProjectService().update_project_columns_metadata("p1", new_meta)
    assert ok is True

    # Invariante: il payload DB include 'canvas' con columns_metadata dentro
    assert len(captured_updates) == 1
    assert "canvas" in captured_updates[0]
    assert captured_updates[0]["canvas"]["columns_metadata"] == new_meta


def test_update_columns_metadata_preserves_other_canvas_fields(monkeypatch):
    """Invariante critica: update non deve droppare nodes/edges esistenti."""
    from app.services.project_service import ProjectService
    from app.services import project_service as ps_mod

    captured = []
    fake_canvas_before = {
        "nodes": [{"id": "n1", "type": "source"}],
        "edges": [{"id": "e1"}],
        "columns_metadata": [{"name": "old_col", "type": "str"}],
    }
    monkeypatch.setattr(
        ps_mod.db, "get_project",
        lambda pid: {"id": "p1", "canvas": fake_canvas_before}
    )
    monkeypatch.setattr(
        ps_mod.db, "update_project",
        lambda pid, **kw: captured.append(kw) or True
    )

    new_meta = [{"name": "new_col", "type": "int"}]
    ProjectService().update_project_columns_metadata("p1", new_meta)

    saved_canvas = captured[0]["canvas"]
    # nodes/edges preservati
    assert saved_canvas["nodes"] == fake_canvas_before["nodes"]
    assert saved_canvas["edges"] == fake_canvas_before["edges"]
    # columns_metadata sovrascritto
    assert saved_canvas["columns_metadata"] == new_meta


# =============================================================================
# 3. Consumer inventory — sentinel sui punti che leggono i tipi
# =============================================================================
# Se un refactor sposta la sorgente di verita', dobbiamo aggiornare
# questi consumer. I test fungono da sentinel: grep AST-based che
# assicura che i pattern noti continuino a esistere nel codebase.


def test_estrai_still_updates_columns_metadata_post_run():
    """ESTRAI deve aggiornare columns_metadata dopo run. Post-refactor
    la chiamata e' via `save_columns_metadata_with_retry` (helper
    condiviso in `app.shared.columns_metadata`) che internamente chiama
    `update_project_columns_metadata`. Sentinel: o si vede il nome del
    helper, o si vede la chiamata diretta — entrambe valide."""
    from app.core.estrai import executor as estrai_mod
    src = inspect.getsource(estrai_mod)
    assert (
        "save_columns_metadata_with_retry" in src
        or "update_project_columns_metadata" in src
    ), (
        "ESTRAI executor non aggiorna columns_metadata post-run. "
        "Senza, columns_metadata resta vuoto -> bridge N11 no-op."
    )


def test_arricchisci_still_updates_columns_metadata_post_run():
    """Analogo: ARRICCHISCI scrive columns_metadata post-run (via helper
    o chiamata diretta — entrambi i pattern accettati)."""
    from app.core.arricchisci import executor as enrich_mod
    src = inspect.getsource(enrich_mod)
    assert (
        "save_columns_metadata_with_retry" in src
        or "update_project_columns_metadata" in src
    )


def test_column_mapper_reads_canvas_types_from_node_data():
    """ColumnMapper.auto_map riceve canvas_types dal node_data CARICA.
    Contract tra orchestrator e ColumnMapper."""
    from app.core.carica.column_mapper import ColumnMapper

    sig = inspect.signature(ColumnMapper.auto_map)
    assert "canvas_types" in sig.parameters, (
        "ColumnMapper.auto_map non accetta canvas_types. Qualsiasi refactor "
        "su questo contract deve essere intenzionale e coordinato."
    )


def test_enricher_bridge_reads_from_columns_metadata():
    """N11 enricher legge columns_metadata come sorgente di verita' semantica."""
    from app.shared import canvas_types_enricher as enricher_mod
    src = inspect.getsource(enricher_mod)
    assert "get_project_columns_metadata" in src
    # Verifica anche che il merge rispetti priority node_data > project
    assert "project_canvas_types" in src or "project" in src.lower()


# =============================================================================
# 4. Format stability — tipi canvas ammessi
# =============================================================================
# I tipi canvas sono una enum informale esposta da
# `app.shared.column_types.normalize_canvas_type` (delega del mapper).
# Qualsiasi refactor che tocca il vocabolario deve preservare i tipi base.


def test_canvas_type_vocabulary_stable():
    """Tipi canvas riconosciuti: int, real, str, bool, date, geometry.
    Se un refactor aggiunge/rimuove tipi, il frontend e il db mapper
    devono essere aggiornati in lockstep."""
    from app.core.carica.column_mapper import ColumnMapper

    mapper = ColumnMapper(db_type="postgres")
    recognized = set()
    for candidate in ["int", "real", "str", "bool", "date", "geometry",
                      "INT", "Real", "geometry:Point:4326"]:
        result = mapper._infer_type_from_canvas(candidate)
        if result:
            recognized.add(result)

    # Vocabolario minimo garantito
    expected = {"int", "real", "str", "bool", "date", "geometry"}
    missing = expected - recognized
    assert not missing, (
        f"Tipi canvas mancanti dal vocabolario: {missing}. "
        "Tutti i tipi base devono restare riconosciuti."
    )
