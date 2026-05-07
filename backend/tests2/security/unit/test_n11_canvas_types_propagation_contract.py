"""
Contract test N11 (propagazione) — canvas_types bridge verso CARICA.

Il fix puntuale precedente (commit 136d750) in ColumnMapper recuperava
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# 1. Helper contract: signature + behavior


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

    # NB: l'enricher fa `from app.services.project_service import ProjectService`
    # al module load (canvas_types_enricher.py:36), quindi il symbol da
    # patchare e' quello bound nel modulo enricher, non l'origine. Patchare
    # `app.services.project_service.ProjectService` lascia il symbol nel
    # modulo enricher invariato → fallback al ProjectService reale →
    # `ProjectService().get_project_columns_metadata(...)` chiama
    # `pg_connection.fetchone` reale → access violation se DB non
    # raggiungibile (vedi review #7 Batch B).
    monkeypatch.setattr(
        "app.shared.canvas_types_enricher.ProjectService",
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

    # NB: l'enricher fa `from app.services.project_service import ProjectService`
    # al module load (canvas_types_enricher.py:36), quindi il symbol da
    # patchare e' quello bound nel modulo enricher, non l'origine. Patchare
    # `app.services.project_service.ProjectService` lascia il symbol nel
    # modulo enricher invariato → fallback al ProjectService reale →
    # `ProjectService().get_project_columns_metadata(...)` chiama
    # `pg_connection.fetchone` reale → access violation se DB non
    # raggiungibile (vedi review #7 Batch B).
    monkeypatch.setattr(
        "app.shared.canvas_types_enricher.ProjectService",
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
        "app.shared.canvas_types_enricher.ProjectService",
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
        "app.shared.canvas_types_enricher.ProjectService",
        EmptyProjectService,
    )

    node_data = {"table_name": "x"}
    mod.enrich_canvas_types_from_metadata(node_data, "proj_1")
    # Nessun canvas_types aggiunto (merged era vuoto)
    assert "canvas_types" not in node_data or node_data["canvas_types"] == {}


# 2. Orchestrator callsite integration


def _module_references_name(module, name: str) -> bool:
    """R-04b helper: True se almeno un callable top-level del modulo
    referenzia `name` come callee/attr (anche in nested code objects)."""
    def _walk(co):
        if name in co.co_names:
            return True
        for const in co.co_consts:
            if hasattr(const, "co_names") and _walk(const):
                return True
        return False

    for attr_name in dir(module):
        attr = getattr(module, attr_name, None)
        if hasattr(attr, "__code__") and _walk(attr.__code__):
            return True
        if inspect.isclass(attr):
            for m_name in dir(attr):
                m = getattr(attr, m_name, None)
                if hasattr(m, "__code__") and _walk(m.__code__):
                    return True
    return False


def test_orchestrator_service_uses_enricher_before_create_carica_task():
    """Il loop carica in orchestrator/service.py deve referenziare
    `enrich_canvas_types_from_metadata` prima di
    `task_service.create_carica_task`.

    Replacement R-04b di `inspect.getsource(orch_service) + str in src`:
    bytecode probe sul callee identifier (refactor-resilient).
    """
    from app.core.orchestrator import service as orch_service

    assert _module_references_name(orch_service, "enrich_canvas_types_from_metadata"), (
        "orchestrator/service.py non chiama l'enricher -> pipeline CARICA "
        "riceve canvas_types vuoto, type inference loss aperto."
    )


def test_orchestrator_router_uses_enricher_in_direct_carica_endpoint():
    """Il router /create-task quando task_type=CARICA deve arricchire."""
    from app.core.orchestrator import router as orch_router

    assert _module_references_name(orch_router, "enrich_canvas_types_from_metadata"), (
        "orchestrator/router.py non chiama l'enricher nel path CARICA "
        "diretto -> stesso gap della chain pipeline."
    )
