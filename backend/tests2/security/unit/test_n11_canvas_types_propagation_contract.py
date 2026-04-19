"""Contract test N11 (propagazione) — canvas_types bridge verso CARICA.

Il fix puntuale precedente (commit 136d750) in ColumnMapper recuperava
int-promoted-to-float euristicamente. Non chiudeva il finding originale
"Type inference loss" che riguardava la PROPAGAZIONE architetturale:
  - projects.columns_metadata e' aggiornato da ESTRAI/ARRICCHISCI dopo
    ogni run con i tipi canvas semantici.
  - Ma l'orchestrator costruiva carica_node_data SENZA leggere
    columns_metadata.
  - Risultato: CARICA riceveva canvas_types vuoto -> cadeva sul dtype
    post-manipolazione (anche col fix euristico, meno preciso di
    columns_metadata).

Fix architetturale (2026-04-17):
  - Helper `enrich_canvas_types_from_metadata(carica_node_data, project_id)`
    in `app/shared/canvas_types_enricher.py`.
  - Applicato in 2 punti:
      * orchestrator/service.py loop carica (pipeline end-to-end)
      * orchestrator/router.py endpoint /create-task CARICA diretto
  - Merge: project columns_metadata come default, node_data prevale
    (frontend autorevole se imposta canvas_types espliciti).

Questo contract congela:
  1. Helper signature + priority merge (node_data > project_metadata)
  2. Orchestrator service usa l'helper prima di creare task CARICA
  3. Router diretto CARICA usa l'helper
  4. Best-effort: lookup project fallisce -> nessuna exception
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# =============================================================================
# 1. Helper contract: signature + behavior
# =============================================================================


def test_enricher_signature_is_stable():
    from app.shared.canvas_types_enricher import enrich_canvas_types_from_metadata

    sig = inspect.signature(enrich_canvas_types_from_metadata)
    params = list(sig.parameters.keys())
    assert params == ["carica_node_data", "project_id"], (
        f"Signature cambiata: {params}. Breaking per i callsite orchestrator."
    )


def test_enricher_populates_canvas_types_from_project_metadata(monkeypatch):
    """columns_metadata -> canvas_types quando node_data e' vuoto."""
    from app.shared import canvas_types_enricher as mod

    fake_metadata = [
        {"name": "id", "type": "int"},
        {"name": "prezzo", "type": "real"},
        {"name": "data", "type": "date"},
    ]

    class FakeProjectService:
        def get_project_columns_metadata(self, pid):
            return fake_metadata

    monkeypatch.setattr(
        "app.services.project_service.ProjectService",
        FakeProjectService,
    )

    node_data = {"table_name": "destinazione"}
    mod.enrich_canvas_types_from_metadata(node_data, "proj_1")
    assert node_data["canvas_types"] == {
        "id": "int", "prezzo": "real", "data": "date",
    }


def test_enricher_respects_node_data_priority_over_project_metadata(monkeypatch):
    """Se frontend ha impostato canvas_types espliciti in node_data,
    NON vengono sovrascritti dal project metadata."""
    from app.shared import canvas_types_enricher as mod

    class FakeProjectService:
        def get_project_columns_metadata(self, pid):
            # Project dice "real" ma node_data ha "int" esplicito per id
            return [{"name": "id", "type": "real"}, {"name": "prezzo", "type": "real"}]

    monkeypatch.setattr(
        "app.services.project_service.ProjectService",
        FakeProjectService,
    )

    node_data = {
        "canvas_types": {"id": "int"},  # frontend autorevole
    }
    mod.enrich_canvas_types_from_metadata(node_data, "proj_1")
    # id resta "int" (node_data); prezzo arriva dal project metadata
    assert node_data["canvas_types"] == {"id": "int", "prezzo": "real"}


def test_enricher_best_effort_on_project_service_failure(monkeypatch):
    """Se ProjectService esplode (DB giu', project non trovato), l'helper
    non deve propagare: node_data resta invariato ma utilizzabile."""
    from app.shared import canvas_types_enricher as mod

    class BrokenProjectService:
        def get_project_columns_metadata(self, pid):
            raise RuntimeError("DB down")

    monkeypatch.setattr(
        "app.services.project_service.ProjectService",
        BrokenProjectService,
    )

    node_data = {"canvas_types": {"x": "int"}}
    # Non deve sollevare
    result = mod.enrich_canvas_types_from_metadata(node_data, "proj_1")
    # node_data invariato (canvas_types gia' presente preserve)
    assert result["canvas_types"] == {"x": "int"}


def test_enricher_handles_empty_metadata(monkeypatch):
    """Project senza columns_metadata -> node_data invariato."""
    from app.shared import canvas_types_enricher as mod

    class EmptyProjectService:
        def get_project_columns_metadata(self, pid):
            return []

    monkeypatch.setattr(
        "app.services.project_service.ProjectService",
        EmptyProjectService,
    )

    node_data = {"table_name": "x"}
    mod.enrich_canvas_types_from_metadata(node_data, "proj_1")
    # Nessun canvas_types aggiunto (merged era vuoto)
    assert "canvas_types" not in node_data or node_data["canvas_types"] == {}


# =============================================================================
# 2. Orchestrator callsite integration
# =============================================================================


def test_orchestrator_service_uses_enricher_before_create_carica_task():
    """Il loop carica in orchestrator/service.py deve chiamare
    enrich_canvas_types_from_metadata prima di task_service.create_carica_task."""
    from app.core.orchestrator import service as orch_service

    src = inspect.getsource(orch_service)
    assert "enrich_canvas_types_from_metadata" in src, (
        "orchestrator/service.py non chiama l'enricher -> pipeline CARICA "
        "riceve canvas_types vuoto, type inference loss aperto."
    )


def test_orchestrator_router_uses_enricher_in_direct_carica_endpoint():
    """Il router /create-task quando task_type=CARICA deve arricchire."""
    from app.core.orchestrator import router as orch_router

    src = inspect.getsource(orch_router)
    assert "enrich_canvas_types_from_metadata" in src, (
        "orchestrator/router.py non chiama l'enricher nel path CARICA "
        "diretto -> stesso gap della chain pipeline."
    )
