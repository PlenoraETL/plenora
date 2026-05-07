"""
[phase-2-err][batch9] C1 — sanitize str(e) in _execute_carica_if_present.

Fix S3 / F1 dell'audit Batch 9: il boundary `OrchestratorService._execute_carica_if_present`
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _make_orchestrator_with_carica_node():
    """Builder: OrchestratorService con manipola_executor mockato a success,
    _find_all_carica_nodes con 1 nodo, canvas mock."""
    from app.core.orchestrator.service import OrchestratorService

    mock_project_service = MagicMock()
    mock_project_service.get_project_metadata.return_value = MagicMock(
        project_type="estrai"
    )
    mock_project_service.get_project_canvas.return_value = MagicMock()

    svc = OrchestratorService(
        project_service=mock_project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
    )
    svc.estrai_executor = MagicMock()
    svc.estrai_executor.execute = MagicMock(
        return_value={"success": True, "result_id": "res-1"}
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
    svc._find_all_carica_nodes = MagicMock(return_value=[fake_carica_node])

    return svc


def test_carica_launch_failure_returns_sanitized_error_string():
    """Quando create_carica_task solleva → artifact["carica"]["error"] e'
    il token canonico 'carica_launch_failed', NON str(e)."""
    svc = _make_orchestrator_with_carica_node()

    sensitive_exc = RuntimeError(
        "postgresql://user:secret@internal-db:5432/config path /internal/xyz"
    )

    with patch(
        "app.services.task_service.task_service.create_carica_task",
        side_effect=sensitive_exc,
    ), patch(
        "app.shared.execution.canvas_types_enricher.enrich_canvas_types_from_metadata"
    ):
        artifact = svc.execute_pipeline("p1")

    assert "carica" in artifact
    carica_info = artifact["carica"]
    assert carica_info["error"] == "carica_launch_failed"
    assert carica_info["message"] == "Errore nell'avvio del caricamento (consulta log)"


def test_carica_launch_failure_does_not_leak_exception_args():
    """Il dict returned NON deve contenere il valore str(e) in nessun campo
    (niente credenziali/path/url interni al FE)."""
    svc = _make_orchestrator_with_carica_node()

    leak_marker = "LEAK_CANARY_a1b2c3d4_secret_password"
    exc = ValueError(leak_marker)

    with patch(
        "app.services.task_service.task_service.create_carica_task",
        side_effect=exc,
    ), patch(
        "app.shared.execution.canvas_types_enricher.enrich_canvas_types_from_metadata"
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
    svc = _make_orchestrator_with_carica_node()
    exc = RuntimeError("tecnicamente-dettagliato-solo-log")

    with patch(
        "app.services.task_service.task_service.create_carica_task",
        side_effect=exc,
    ), patch(
        "app.shared.execution.canvas_types_enricher.enrich_canvas_types_from_metadata"
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
    """Sanity: se create_carica_task ha successo, il dict CARICA continua
    ad avere la shape legacy `{tasks: [...], message: ...}` — il fix non
    tocca il path success."""
    svc = _make_orchestrator_with_carica_node()

    fake_task_info = MagicMock()
    fake_task_info.task_id = "ct-xyz"

    with patch(
        "app.services.task_service.task_service.create_carica_task",
        return_value=fake_task_info,
    ), patch(
        "app.shared.execution.canvas_types_enricher.enrich_canvas_types_from_metadata"
    ):
        artifact = svc.execute_pipeline("p1")

    carica_info = artifact["carica"]
    assert "tasks" in carica_info
    assert carica_info["tasks"][0]["task_id"] == "ct-xyz"
    assert carica_info["tasks"][0]["node_id"] == "carica-node-1"
    assert "error" not in carica_info
