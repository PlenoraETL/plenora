"""Parita' dispatch CARICA tra path enrichment e path orchestrator.

Il trigger CARICA dal task di enrichment (`_trigger_carica_if_present`) deve
applicare le STESSE protezioni del path orchestrator
(`OrchestratorService._execute_carica_if_present`):

1. Safe-merge di ``data{}`` che NON sovrascrive i campi identita' riservati
   del nodo (id/type/...).
2. Enrichment di ``canvas_types`` dal contratto pubblico del progetto
   (output_contract), con i tipi gia' presenti sul nodo prioritari.

Pre-fix questi due comportamenti erano presenti solo nell'orchestrator: un
nodo CARICA a valle di un ARRICCHISCI riceveva un trattamento piu' debole.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests2.shared._ast_identifiers import all_identifiers, uses_attribute


pytestmark = [pytest.mark.unit, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _canvas_with_carica_node() -> dict:
    """Canvas minimale: result -> carica, con campi insidiosi in data{}."""
    return {
        "nodes": [
            {"id": "out", "type": "result"},
            {
                "id": "carica-1",
                "type": "carica",
                "data": {
                    # campo riservato: deve essere IGNORATO dal merge
                    "type": "EVIL_OVERRIDE",
                    # campo non riservato: deve essere mantenuto
                    "connection_id": "conn-1",
                    # tipo a livello nodo: deve avere priorita' sul contratto
                    "canvas_types": {"col_b": "geometry"},
                },
            },
        ],
        "edges": [{"source": "out", "target": "carica-1"}],
    }


def _run_trigger_capturing_payload():
    """Esegue _trigger_carica_if_present con dipendenze mockate e ritorna il
    `carica_node_data` con cui sarebbe stato lanciato il task CARICA."""
    from workers.tasks import enrichment_task

    captured: dict = {}

    def _fake_submit(req):
        captured["req"] = req
        info = MagicMock()
        info.task_id = "ct-1"
        return info

    fake_project_service = MagicMock()
    fake_project_service.get_project_output_contract.return_value = object()

    with patch(
        "app.core.orchestrator.task_service.task_service.submit_task",
        side_effect=_fake_submit,
    ), patch(
        "app.core.projects.service.ProjectService",
        return_value=fake_project_service,
    ), patch(
        "app.core.output_contract.column_types_from_contract",
        return_value={"col_a": "integer", "col_b": "string"},
    ):
        result = enrichment_task._trigger_carica_if_present(
            project_id="p1",
            canvas=_canvas_with_carica_node(),
            output_format="csv",
        )

    assert result is not None and result["tasks"][0]["task_id"] == "ct-1"
    return captured["req"].payload.carica_node_data


def test_enrichment_carica_dispatch_protects_reserved_node_fields():
    """data{} non puo' sovrascrivere i campi identita' del nodo."""
    node_data = _run_trigger_capturing_payload()
    # 'type' resta quello del nodo, non l'EVIL_OVERRIDE messo in data{}
    assert node_data["type"] == "carica"
    # i campi non riservati di data{} restano disponibili
    assert node_data["connection_id"] == "conn-1"


def test_enrichment_carica_dispatch_enriches_canvas_types_from_contract():
    """canvas_types arricchito dal contratto, con priorita' al nodo."""
    node_data = _run_trigger_capturing_payload()
    canvas_types = node_data["canvas_types"]
    # dal contratto pubblico
    assert canvas_types["col_a"] == "integer"
    # priorita' al tipo gia' presente sul nodo (geometry vince su string)
    assert canvas_types["col_b"] == "geometry"


def test_carica_dispatch_runtime_paths_delegate_to_shared_launcher():
    """Orchestrator ed enrichment devono DELEGARE allo stesso launcher canonico.

    Post-unificazione executor canonico: il loop find/build/lancio CARICA vive
    in un'unica funzione ``task_service.launch_carica_tasks_for_canvas``. I due
    call-site (orchestrator + enrichment) non devono piu' riscriverlo a mano —
    devono solo chiamare il launcher. Garanzia piu' forte del precedente
    "usano gli stessi sub-helper"."""
    orchestrator_src = (
        _BACKEND_ROOT / "app" / "core" / "orchestrator" / "service.py"
    ).read_text(encoding="utf-8")
    enrichment_src = (
        _BACKEND_ROOT / "workers" / "tasks" / "enrichment_task.py"
    ).read_text(encoding="utf-8")
    task_service_src = (
        _BACKEND_ROOT / "app" / "core" / "orchestrator" / "task_service.py"
    ).read_text(encoding="utf-8")

    for src in (orchestrator_src, enrichment_src):
        # delega al launcher unico
        assert "launch_carica_tasks_for_canvas" in all_identifiers(src)
        # il loop NON deve essere riscritto nei due call-site
        assert not uses_attribute(src, "carica_dispatcher", "find_connected_carica_nodes")
        assert not uses_attribute(src, "carica_dispatcher", "build_carica_node_data")

    # il loop canonico vive nel launcher condiviso (unica fonte di verita')
    assert uses_attribute(task_service_src, "carica_dispatcher", "find_connected_carica_nodes")
    assert uses_attribute(task_service_src, "carica_dispatcher", "build_carica_node_data")

    assert "NODE_RESERVED_FIELDS" not in all_identifiers(orchestrator_src)
    assert "NODE_RESERVED_FIELDS" not in all_identifiers(enrichment_src)
