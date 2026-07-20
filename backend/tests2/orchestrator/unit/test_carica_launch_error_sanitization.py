"""
C1 — sanitize str(e) in _execute_carica_if_present.

Fix S3 / F1 dell'Audit finding: il boundary `OrchestratorService._execute_carica_if_present`
deve restituire un errore sanitizzato (token canonico, niente str(e)) quando il
lancio del task CARICA fallisce.

Post-unificazione executor canonico: il loop di lancio CARICA vive ora nel
launcher condiviso ``task_service.launch_carica_tasks_for_canvas`` (chiamato con
``resilient=False`` dall'orchestrator). L'orchestrator mantiene il wrapper
all-or-nothing che traduce qualsiasi eccezione di lancio in
``carica_launch_failed``. I test patchano ``find_connected_carica_nodes`` (prima
mockavano il rimosso ``svc._find_all_carica_nodes``).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class _ExecutorStub:
    def __init__(self, result=None):
        self._result = result if result is not None else {"success": True}

    def execute(self, *args, **kwargs):
        return self._result

    def preview(self, *args, **kwargs):
        return {"success": True, "rows": []}


def _make_orchestrator_with_carica_node():
    """Builder: OrchestratorService con estrai_executor a success + un nodo
    CARICA fittizio. Ritorna ``(svc, fake_carica_node)``; i test patchano
    ``find_connected_carica_nodes`` per restituire il nodo."""
    from app.core.orchestrator.service import OrchestratorService

    mock_project_service = MagicMock()
    mock_project_service.get_project_metadata.return_value = MagicMock(
        project_type="estrai",
        group_id="group-1",
    )
    mock_project_service.get_project_canvas.return_value = {
        "nodes": [],
        "edges": [],
    }

    estrai_executor = _ExecutorStub({"success": True, "result_id": "res-1"})
    svc = OrchestratorService(
        project_service=mock_project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        estrai_executor=estrai_executor,
        manipola_executor=_ExecutorStub(),
        enrich_executor=_ExecutorStub(),
    )

    # 1 nodo CARICA fittizio collegato all'output
    fake_carica_node = MagicMock()
    fake_carica_node.id = "carica-node-1"
    fake_carica_node.type = "carica"
    fake_carica_node.data = {"connection_id": "conn-1", "dest_type": "pg"}
    fake_carica_node.model_dump = MagicMock(return_value={
        "id": "carica-node-1",
        "type": "carica",
        "data": {"connection_id": "conn-1", "dest_type": "pg"},
    })

    return svc, fake_carica_node


def _patch_find(fake_carica_node):
    """Patch del find condiviso usato dal launcher canonico."""
    return patch(
        "app.shared.execution.carica_dispatcher.find_connected_carica_nodes",
        return_value=[fake_carica_node],
    )


def test_carica_launch_failure_returns_sanitized_error_string():
    """Quando il lancio del task solleva → artifact["carica"]["error"] e'
    il token canonico 'carica_launch_failed', NON str(e)."""
    svc, fake_node = _make_orchestrator_with_carica_node()

    sensitive_exc = RuntimeError(
        "postgresql://user:secret@internal-db:5432/config path /internal/xyz"
    )

    with _patch_find(fake_node), patch(
        "app.core.orchestrator.task_service.task_service.submit_task",
        side_effect=sensitive_exc,
    ), patch(
        "app.shared.execution.output_contract_types_enricher.enrich_canvas_types_from_output_contract"
    ):
        artifact = svc.execute_pipeline("p1")

    assert "carica" in artifact
    carica_info = artifact["carica"]
    assert carica_info["error"] == "carica_launch_failed"
    assert carica_info["message"] == "Errore nell'avvio del caricamento (consulta log)"


def test_carica_launch_failure_does_not_leak_exception_args():
    """Il dict returned NON deve contenere il valore str(e) in nessun campo
    (niente credenziali/path/url interni al FE)."""
    svc, fake_node = _make_orchestrator_with_carica_node()

    leak_marker = "LEAK_CANARY_a1b2c3d4_secret_password"
    exc = ValueError(leak_marker)

    with _patch_find(fake_node), patch(
        "app.core.orchestrator.task_service.task_service.submit_task",
        side_effect=exc,
    ), patch(
        "app.shared.execution.output_contract_types_enricher.enrich_canvas_types_from_output_contract"
    ):
        artifact = svc.execute_pipeline("p1")

    carica_info = artifact["carica"]
    # nessuna chiave del dict carica deve contenere il canary
    for k, v in carica_info.items():
        assert leak_marker not in str(v), (
            f"Leak rilevato: campo '{k}' contiene il canary"
        )


def test_carica_launch_failure_logs_exception_with_exc_info():
    """Il dettaglio tecnico sanitizzato fuori dalla response deve restare
    presente nel log backend (exc_info=True gia' in posto)."""
    svc, fake_node = _make_orchestrator_with_carica_node()
    exc = RuntimeError("tecnicamente-dettagliato-solo-log")

    with _patch_find(fake_node), patch(
        "app.core.orchestrator.task_service.task_service.submit_task",
        side_effect=exc,
    ), patch(
        "app.shared.execution.output_contract_types_enricher.enrich_canvas_types_from_output_contract"
    ), patch(
        "app.core.orchestrator.service.logger"
    ) as mock_logger:
        svc.execute_pipeline("p1")

    # il logger custom del modulo deve aver ricevuto almeno una error()
    # con il marker [CRASH] CARICA e exc_info=True
    crash_calls = [
        call for call in mock_logger.error.call_args_list
        if "[CRASH]" in str(call) and "CARICA" in str(call)
    ]
    assert crash_calls, (
        f"atteso almeno un logger.error('[CRASH] ... CARICA ...'), "
        f"ricevuto: {mock_logger.error.call_args_list}"
    )
    # exc_info=True deve essere il kwarg del log boundary (traceback preservato)
    assert any(
        call.kwargs.get("exc_info") is True for call in crash_calls
    ), "exc_info=True atteso sul log [CRASH] CARICA"


def test_carica_launch_success_unchanged_contract():
    """Sanity: se il lancio ha successo, il dict CARICA continua ad avere la
    shape legacy `{tasks: [...], message: ...}` — il fix non tocca il path
    success."""
    svc, fake_node = _make_orchestrator_with_carica_node()

    fake_task_info = MagicMock()
    fake_task_info.task_id = "ct-xyz"

    with _patch_find(fake_node), patch(
        "app.core.orchestrator.task_service.task_service.submit_task",
        return_value=fake_task_info,
    ), patch(
        "app.shared.execution.output_contract_types_enricher.enrich_canvas_types_from_output_contract"
    ):
        artifact = svc.execute_pipeline("p1")

    carica_info = artifact["carica"]
    assert "tasks" in carica_info
    assert carica_info["tasks"][0]["task_id"] == "ct-xyz"
    assert carica_info["tasks"][0]["node_id"] == "carica-node-1"
    assert "error" not in carica_info
