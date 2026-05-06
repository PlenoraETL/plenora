"""Contract tests for ExecutionResult typed boundary value.

BE-GO-READY-ARCH-057-W3: validates the from_executor_dict / to_dict
round-trip and the typed properties that replace raw dict guessing at
the OrchestratorService -> PipelineArtifact boundary.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.shared.execution.executor_protocol import ExecutionResult


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# ---------------------------------------------------------------------------
# from_executor_dict - happy path (success)
# ---------------------------------------------------------------------------

def test_from_dict_success_extracts_typed_fields():
    d = {"success": True, "result_id": "r-1", "rows_count": 42}
    r = ExecutionResult.from_executor_dict(d)
    assert r.success is True
    assert r.message is None
    assert r.error is None
    assert r.payload == {"result_id": "r-1", "rows_count": 42}


def test_from_dict_failure_with_message_key():
    d = {"success": False, "message": "Sorgente non raggiungibile"}
    r = ExecutionResult.from_executor_dict(d)
    assert r.success is False
    assert r.message == "Sorgente non raggiungibile"
    assert r.error is None
    assert r.payload == {}


def test_from_dict_failure_with_error_key():
    """Arricchisci-style: uses 'error' not 'message' for failures."""
    d = {"success": False, "error": "Errore chiamata API upstream: 500", "user_error": True}
    r = ExecutionResult.from_executor_dict(d)
    assert r.success is False
    assert r.error == "Errore chiamata API upstream: 500"
    assert r.message is None
    assert r.payload == {"user_error": True}


def test_from_dict_payload_preserves_all_extra_keys():
    d = {
        "success": True,
        "result_id": "r-2",
        "output_path": "/results/out.csv",
        "filename": "out.csv",
        "format": "csv",
        "rows_count": 10,
        "run_id": "run-abc",
    }
    r = ExecutionResult.from_executor_dict(d)
    assert r.payload == {
        "result_id": "r-2",
        "output_path": "/results/out.csv",
        "filename": "out.csv",
        "format": "csv",
        "rows_count": 10,
        "run_id": "run-abc",
    }


# ---------------------------------------------------------------------------
# from_executor_dict - non-dict input
# ---------------------------------------------------------------------------

def test_from_dict_non_dict_returns_failed_result():
    r = ExecutionResult.from_executor_dict(None)
    assert r.success is False
    assert r.message == "result non strutturato"
    assert r.payload == {}

    r2 = ExecutionResult.from_executor_dict("bad")
    assert r2.success is False


# ---------------------------------------------------------------------------
# to_dict - round-trip fidelity
# ---------------------------------------------------------------------------

def test_to_dict_success_round_trip():
    original = {"success": True, "result_id": "r-1", "rows_count": 42}
    assert ExecutionResult.from_executor_dict(original).to_dict() == original


def test_to_dict_failure_with_message_round_trip():
    original = {"success": False, "message": "Errore sorgente"}
    assert ExecutionResult.from_executor_dict(original).to_dict() == original


def test_to_dict_failure_with_error_round_trip():
    original = {"success": False, "error": "500 upstream", "user_error": True}
    assert ExecutionResult.from_executor_dict(original).to_dict() == original


def test_to_dict_omits_none_message_and_error():
    r = ExecutionResult(success=True, payload={"result_id": "r-x"})
    d = r.to_dict()
    assert "message" not in d
    assert "error" not in d
    assert d == {"success": True, "result_id": "r-x"}


def test_to_dict_payload_keys_not_duplicated():
    """Payload keys appear once even when success/message/error not present."""
    r = ExecutionResult(success=True, payload={"a": 1, "b": 2})
    d = r.to_dict()
    assert list(d.keys()).count("a") == 1
    assert d == {"success": True, "a": 1, "b": 2}


# ---------------------------------------------------------------------------
# Frozen dataclass invariants
# ---------------------------------------------------------------------------

def test_execution_result_is_frozen():
    r = ExecutionResult(success=True)
    with pytest.raises((AttributeError, TypeError)):
        r.success = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Integration: orchestrator uses ExecutionResult at the boundary
# ---------------------------------------------------------------------------

def test_orchestrator_execute_pipeline_passes_through_via_execution_result():
    """execute_pipeline wraps executor dict via ExecutionResult before
    constructing PipelineArtifact - payload keys survive unchanged."""
    from app.core.orchestrator.service import OrchestratorService
    from app.core.artifacts.types import PipelineArtifact

    mock_ps = MagicMock()
    mock_ps.get_project_metadata.return_value = MagicMock(project_type="estrai")
    mock_ps.get_project_canvas.return_value = MagicMock()

    svc = OrchestratorService(
        project_service=mock_ps,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
    )
    svc.estrai_executor = MagicMock()
    svc.estrai_executor.execute.return_value = {
        "success": True,
        "result_id": "res-e-1",
        "rows_count": 5,
        "format": "csv",
    }
    svc._find_all_carica_nodes = MagicMock(return_value=[])

    artifact = svc.execute_pipeline("proj-x")

    assert isinstance(artifact, PipelineArtifact)
    assert artifact.success is True
    assert artifact.result_id == "res-e-1"
    assert artifact["rows_count"] == 5
    assert artifact["format"] == "csv"


def test_orchestrator_execute_pipeline_error_key_preserved():
    """Arricchisci-style 'error' key survives the ExecutionResult boundary."""
    from app.core.orchestrator.service import OrchestratorService
    from app.core.artifacts.types import PipelineArtifact

    mock_ps = MagicMock()
    mock_ps.get_project_metadata.return_value = MagicMock(project_type="arricchisci")
    mock_ps.get_project_canvas.return_value = MagicMock()

    svc = OrchestratorService(
        project_service=mock_ps,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
    )
    svc.enrich_executor = MagicMock()
    svc.enrich_executor.execute.return_value = {
        "success": False,
        "error": "Errore chiamata API upstream: timeout",
        "user_error": True,
    }
    svc._find_all_carica_nodes = MagicMock(return_value=[])

    artifact = svc.execute_pipeline("proj-y")

    assert isinstance(artifact, PipelineArtifact)
    assert artifact.success is False
    assert artifact["error"] == "Errore chiamata API upstream: timeout"
    assert artifact["user_error"] is True
