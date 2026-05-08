"""N11 canvas_types propagation contract tests."""

from __future__ import annotations

import inspect

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def test_enricher_signature_is_stable():
    from app.shared.execution.canvas_types_enricher import enrich_canvas_types_from_metadata

    sig = inspect.signature(enrich_canvas_types_from_metadata)
    params = list(sig.parameters.keys())
    assert params == ["carica_node_data", "project_id", "columns_metadata_loader"]


def test_enricher_populates_canvas_types_from_project_metadata():
    from app.shared.execution import canvas_types_enricher as mod

    fake_metadata = [
        {"name": "id", "type": "int"},
        {"name": "prezzo", "type": "real"},
        {"name": "data", "type": "date"},
    ]

    node_data = {"table_name": "destinazione"}
    mod.enrich_canvas_types_from_metadata(node_data, "proj_1", lambda _pid: fake_metadata)
    assert node_data["canvas_types"] == {
        "id": "int",
        "prezzo": "real",
        "data": "date",
    }


def test_enricher_respects_node_data_priority_over_project_metadata():
    from app.shared.execution import canvas_types_enricher as mod

    node_data = {"canvas_types": {"id": "int"}}
    mod.enrich_canvas_types_from_metadata(
        node_data,
        "proj_1",
        lambda _pid: [{"name": "id", "type": "real"}, {"name": "prezzo", "type": "real"}],
    )
    assert node_data["canvas_types"] == {"id": "int", "prezzo": "real"}


def test_enricher_best_effort_on_project_service_failure():
    from app.shared.execution import canvas_types_enricher as mod

    def broken_loader(_pid):
        raise RuntimeError("DB down")

    node_data = {"canvas_types": {"x": "int"}}
    result = mod.enrich_canvas_types_from_metadata(node_data, "proj_1", broken_loader)
    assert result["canvas_types"] == {"x": "int"}


def test_enricher_handles_empty_metadata():
    from app.shared.execution import canvas_types_enricher as mod

    node_data = {"table_name": "x"}
    mod.enrich_canvas_types_from_metadata(node_data, "proj_1", lambda _pid: [])
    assert "canvas_types" not in node_data or node_data["canvas_types"] == {}


def _module_references_name(module, name: str) -> bool:
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
                method = getattr(attr, m_name, None)
                if hasattr(method, "__code__") and _walk(method.__code__):
                    return True
    return False


def test_orchestrator_service_uses_enricher_before_create_carica_task():
    from app.core.orchestrator import service as orch_service

    assert _module_references_name(orch_service, "enrich_canvas_types_from_metadata")


def test_orchestrator_router_uses_enricher_in_direct_carica_endpoint():
    from app.core.orchestrator import router as orch_router

    assert _module_references_name(orch_router, "enrich_canvas_types_from_metadata")
