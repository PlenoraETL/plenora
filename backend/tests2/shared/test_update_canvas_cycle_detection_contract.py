"""
Integrazione detect_project_lineage_cycle con ProjectService.update_canvas.

Contratto:
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.project_lineage import ProjectLineageCycleError
from app.core.projects.service import ProjectService
from app.shared.models import CanvasState, Node, Edge


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
    """Mock di app.db.config.projects per get_project/update_project.

    canvases: dict {project_id: canvas_dict_or_None}. None = progetto
    esistente ma senza canvas.
    """
    module = MagicMock()

    def get_project(pid):
        if pid not in canvases:
            return None
        return {"id": pid, "group_id": "grp-1", "canvas": canvases[pid]}

    def update_project(pid, **kwargs):
        if "canvas" in kwargs:
            canvases[pid] = kwargs["canvas"]

    module.get_project.side_effect = get_project
    module.update_project.side_effect = update_project
    return module


# Happy path


def test_update_canvas_accepts_canvas_without_project_sources():
    """Canvas senza project_source: nessun check, save diretto."""
    canvas = CanvasState(nodes=[], edges=[])
    canvases = {"A": None}

    with patch("app.core.projects.service.project_db", _fake_project_db_with(canvases)):
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

    with patch("app.core.projects.service.project_db", _fake_project_db_with(canvases)):
        svc = ProjectService()
        result = svc.update_canvas("A", new_canvas_A)
        assert result is True


# Cycle rejection


def test_update_canvas_rejects_self_reference():
    """A.canvas ha project_source → A. Save rifiutato."""
    canvases = {"A": None}
    new_canvas_A = _canvas_with_sources(["A"])

    with patch("app.core.projects.service.project_db", _fake_project_db_with(canvases)):
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

    with patch("app.core.projects.service.project_db", _fake_project_db_with(canvases)):
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

    with patch("app.core.projects.service.project_db", _fake_project_db_with(canvases)):
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

    with patch("app.core.projects.service.project_db", _fake_project_db_with(canvases)):
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

    with patch("app.core.projects.service.project_db", _fake_project_db_with(canvases)):
        svc = ProjectService()
        result = svc.update_canvas("A", new_canvas_A)
        assert result is True
