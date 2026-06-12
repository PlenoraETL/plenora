"""
Integrazione detect_project_lineage_cycle con ProjectService.update_canvas.

Contratto:
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.projects.lineage import ProjectLineageCycleError
from app.core.projects.service import ProjectService
from app.core.projects.canvas_models import CanvasState, Node


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _canvas_with_sources(source_project_ids):
    """Costruisce un CanvasState con N nodi project_source."""
    nodes = []
    for i, pid in enumerate(source_project_ids):
        nodes.append(Node(
            id=f"ps-{i}",
            type="project_source",
            source_project_id=pid,
            position={"x": 0, "y": 0},
        ))
    return CanvasState(nodes=nodes, edges=[])


def _fake_project_db_with(canvases):
    """Mock di app.core.projects.db per get_project_row/update_project_row.

    canvases: dict {project_id: canvas_dict_or_None}. None = progetto
    esistente ma senza canvas.
    """
    module = MagicMock()

    def get_project_row(pid, group_id=None):
        if pid not in canvases:
            return None
        return SimpleNamespace(id=pid, group_id="grp-1", canvas=canvases[pid])

    def update_project_row(pid, **kwargs):
        if "canvas" in kwargs:
            canvases[pid] = kwargs["canvas"]
        if pid not in canvases:
            return None
        return SimpleNamespace(id=pid, group_id="grp-1", canvas=canvases[pid])

    module.get_project_row.side_effect = get_project_row
    module.update_project_row.side_effect = update_project_row
    return module


# Happy path


def test_update_canvas_accepts_canvas_without_project_sources():
    """Canvas senza project_source: nessun check, save diretto."""
    canvas = CanvasState(nodes=[], edges=[])
    canvases = {"A": None}

    # Epic F (F3/F5): update_canvas vive nel sub-service canvas e il
    # cascade interroga il sub-service lifecycle — il fake db va patchato
    # su tutti e tre i namespace (facade, canvas_service, lifecycle_service).
    _fake_db = _fake_project_db_with(canvases)
    with patch("app.core.projects.service.project_db", _fake_db), \
         patch("app.core.projects.canvas_service.project_db", _fake_db), \
         patch("app.core.projects.lifecycle_service.project_db", _fake_db):
        svc = ProjectService()
        assert svc.update_canvas("A", canvas) is True


def test_update_canvas_accepts_linear_chain_without_cycle():
    """A → B → C: save di A con project_source=B, B ha project_source=C."""
    canvases = {
        "A": None,
        "B": {"nodes": [{"id": "ps", "type": "project_source",
                         "source_project_id": "C"}], "edges": []},
        "C": {"nodes": [], "edges": []},
    }
    new_canvas_A = _canvas_with_sources(["B"])

    # Epic F (F3/F5): update_canvas vive nel sub-service canvas e il
    # cascade interroga il sub-service lifecycle — il fake db va patchato
    # su tutti e tre i namespace (facade, canvas_service, lifecycle_service).
    _fake_db = _fake_project_db_with(canvases)
    with patch("app.core.projects.service.project_db", _fake_db), \
         patch("app.core.projects.canvas_service.project_db", _fake_db), \
         patch("app.core.projects.lifecycle_service.project_db", _fake_db):
        svc = ProjectService()
        result = svc.update_canvas("A", new_canvas_A)
        assert result is True


# Cycle rejection


def test_update_canvas_rejects_self_reference():
    """A.canvas ha project_source → A. Save rifiutato."""
    canvases = {"A": None}
    new_canvas_A = _canvas_with_sources(["A"])

    # Epic F (F3/F5): update_canvas vive nel sub-service canvas e il
    # cascade interroga il sub-service lifecycle — il fake db va patchato
    # su tutti e tre i namespace (facade, canvas_service, lifecycle_service).
    _fake_db = _fake_project_db_with(canvases)
    with patch("app.core.projects.service.project_db", _fake_db), \
         patch("app.core.projects.canvas_service.project_db", _fake_db), \
         patch("app.core.projects.lifecycle_service.project_db", _fake_db):
        svc = ProjectService()
        with pytest.raises(ProjectLineageCycleError) as exc:
            svc.update_canvas("A", new_canvas_A)

    assert "A" in exc.value.chain


def test_update_canvas_rejects_a_to_b_to_a_cycle():
    """A's nuovo canvas → B, B's canvas esistente → A. Save di A fallisce."""
    canvases = {
        "A": None,
        "B": {"nodes": [{"id": "ps", "type": "project_source",
                         "source_project_id": "A"}], "edges": []},
    }
    new_canvas_A = _canvas_with_sources(["B"])

    # Epic F (F3/F5): update_canvas vive nel sub-service canvas e il
    # cascade interroga il sub-service lifecycle — il fake db va patchato
    # su tutti e tre i namespace (facade, canvas_service, lifecycle_service).
    _fake_db = _fake_project_db_with(canvases)
    with patch("app.core.projects.service.project_db", _fake_db), \
         patch("app.core.projects.canvas_service.project_db", _fake_db), \
         patch("app.core.projects.lifecycle_service.project_db", _fake_db):
        svc = ProjectService()
        with pytest.raises(ProjectLineageCycleError) as exc:
            svc.update_canvas("A", new_canvas_A)

    chain = exc.value.chain
    assert "A" in chain
    assert "B" in chain


def test_cycle_error_message_is_readable():
    """Il messaggio dell'errore deve contenere la catena con arrow."""
    canvases = {"A": None}
    new_canvas_A = _canvas_with_sources(["A"])

    # Epic F (F3/F5): update_canvas vive nel sub-service canvas e il
    # cascade interroga il sub-service lifecycle — il fake db va patchato
    # su tutti e tre i namespace (facade, canvas_service, lifecycle_service).
    _fake_db = _fake_project_db_with(canvases)
    with patch("app.core.projects.service.project_db", _fake_db), \
         patch("app.core.projects.canvas_service.project_db", _fake_db), \
         patch("app.core.projects.lifecycle_service.project_db", _fake_db):
        svc = ProjectService()
        with pytest.raises(ProjectLineageCycleError) as exc:
            svc.update_canvas("A", new_canvas_A)

    assert "A" in str(exc.value)
    assert "→" in str(exc.value)


# Missing project handling


def test_update_canvas_returns_false_for_missing_project():
    """Retrocompat: se il progetto non esiste, ritorna False (non raise)."""
    canvases = {}  # A non esiste
    canvas = CanvasState(nodes=[], edges=[])

    # Epic F (F3/F5): update_canvas vive nel sub-service canvas e il
    # cascade interroga il sub-service lifecycle — il fake db va patchato
    # su tutti e tre i namespace (facade, canvas_service, lifecycle_service).
    _fake_db = _fake_project_db_with(canvases)
    with patch("app.core.projects.service.project_db", _fake_db), \
         patch("app.core.projects.canvas_service.project_db", _fake_db), \
         patch("app.core.projects.lifecycle_service.project_db", _fake_db):
        svc = ProjectService()
        assert svc.update_canvas("A", canvas) is False


def test_cycle_detection_ignores_unreachable_projects():
    """Solo le dipendenze raggiungibili dal canvas salvato contano."""
    canvases = {
        "A": None,
        "B": {"nodes": [], "edges": []},
        # X → Y → X ciclo separato (irraggiungibile da A)
        "X": {"nodes": [{"id": "ps", "type": "project_source",
                         "source_project_id": "Y"}], "edges": []},
        "Y": {"nodes": [{"id": "ps", "type": "project_source",
                         "source_project_id": "X"}], "edges": []},
    }
    new_canvas_A = _canvas_with_sources(["B"])

    # Epic F (F3/F5): update_canvas vive nel sub-service canvas e il
    # cascade interroga il sub-service lifecycle — il fake db va patchato
    # su tutti e tre i namespace (facade, canvas_service, lifecycle_service).
    _fake_db = _fake_project_db_with(canvases)
    with patch("app.core.projects.service.project_db", _fake_db), \
         patch("app.core.projects.canvas_service.project_db", _fake_db), \
         patch("app.core.projects.lifecycle_service.project_db", _fake_db):
        svc = ProjectService()
        result = svc.update_canvas("A", new_canvas_A)
        assert result is True
