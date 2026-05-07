"""
Contract tests for ExecutionResult typed boundary value.

BE-GO-READY-ARCH-057-W3: validates the from_executor_dict / to_dict
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.shared.execution.executor_protocol import ExecutionResult, ArtifactPayload


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# from_executor_dict - happy path (success)

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


# from_executor_dict - non-dict input

def test_from_dict_non_dict_returns_failed_result():
    r = ExecutionResult.from_executor_dict(None)
    assert r.success is False
    assert r.message == "result non strutturato"
    assert r.payload == {}

    r2 = ExecutionResult.from_executor_dict("bad")
    assert r2.success is False


# to_dict - round-trip fidelity

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


# Frozen dataclass invariants

def test_execution_result_is_frozen():
    r = ExecutionResult(success=True)
    with pytest.raises((AttributeError, TypeError)):
        r.success = False  # type: ignore[misc]


# Integration: orchestrator uses ExecutionResult at the boundary

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


def test_pipeline_artifact_consumes_execution_result_without_to_dict(monkeypatch):
    """Internal artifact factory consumes ExecutionResult natively."""
    from app.core.artifacts.types import PipelineArtifact

    def fail_to_dict(_result: ExecutionResult) -> dict[str, object]:
        raise AssertionError("internal boundary called ExecutionResult.to_dict()")

    monkeypatch.setattr(ExecutionResult, "to_dict", fail_to_dict)

    artifact = PipelineArtifact.from_execution_result(
        ExecutionResult(
            success=True,
            message="ok",
            payload={"result_id": "res-native", "rows_count": 3},
        ),
        project_id="proj-native",
    )

    assert artifact.success is True
    assert artifact.message == "ok"
    assert artifact.result_id == "res-native"
    assert artifact["rows_count"] == 3


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


# Payload guard - BE-GO-READY-ARCH-058-W4


def test_payload_guard_rejects_pandas_dataframe():
    """DataFrame in payload raises ValueError without importing pandas at module level."""
    import pandas as pd
    df = pd.DataFrame({"a": [1, 2]})
    with pytest.raises(ValueError, match="DataFrame"):
        ExecutionResult(success=True, payload={"df": df})


def test_payload_guard_rejects_geopandas_geodataframe():
    """GeoDataFrame in payload raises ValueError (geopandas module detected)."""
    import geopandas as gpd
    from shapely.geometry import Point
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[Point(0, 0)])
    with pytest.raises(ValueError, match="GeoDataFrame"):
        ExecutionResult(success=True, payload={"gdf": gdf})


def test_payload_guard_rejects_bytes():
    """Raw bytes in payload raises ValueError."""
    with pytest.raises(ValueError, match="bytes"):
        ExecutionResult(success=True, payload={"blob": b"raw data"})


def test_payload_guard_rejects_bytearray():
    """Bytearray in payload raises ValueError."""
    with pytest.raises(ValueError, match="bytearray"):
        ExecutionResult(success=True, payload={"blob": bytearray(b"raw")})


def test_payload_guard_rejects_file_like_stream():
    """File-like stream (with read()) in payload raises ValueError."""
    import io
    buf = io.BytesIO(b"data")
    with pytest.raises(ValueError, match="file-like"):
        ExecutionResult(success=True, payload={"stream": buf})


def test_payload_guard_rejects_via_from_executor_dict():
    """from_executor_dict also triggers the guard (goes through __post_init__)."""
    import pandas as pd
    df = pd.DataFrame({"x": [1]})
    d = {"success": True, "dataframe": df}
    with pytest.raises(ValueError, match="DataFrame"):
        ExecutionResult.from_executor_dict(d)


def test_payload_guard_allows_safe_scalars():
    """Safe scalar payloads pass: str, int, float, bool, None, list, dict."""
    r = ExecutionResult(
        success=True,
        payload={
            "result_id": "r-1",
            "output_path": "/results/out.csv",
            "filename": "out.csv",
            "format": "csv",
            "rows_count": 42,
            "run_id": "run-abc",
            "tags": ["a", "b"],
            "meta": {"crs": 4326},
            "flag": True,
            "ratio": 0.95,
            "empty": None,
        },
    )
    assert r.payload["result_id"] == "r-1"
    assert r.payload["rows_count"] == 42


def test_payload_guard_empty_payload_allowed():
    """Empty payload dict must not raise."""
    r = ExecutionResult(success=True)
    assert r.payload == {}


# ArtifactPayload typed accessor - BE-GO-READY-ARCH-059-W3


def test_artifact_payload_from_success_result_typed_fields():
    """Success ExecutionResult exposes typed ArtifactPayload with known fields."""
    r = ExecutionResult.from_executor_dict({
        "success": True,
        "result_id": "res-1",
        "output_path": "/out/file.csv",
        "filename": "file.csv",
        "format": "csv",
        "rows_count": 42,
        "run_id": "run-abc",
    })
    ap = r.artifact_payload()
    assert isinstance(ap, ArtifactPayload)
    assert ap.result_id == "res-1"
    assert ap.output_path == "/out/file.csv"
    assert ap.filename == "file.csv"
    assert ap.format == "csv"
    assert ap.rows_count == 42
    assert ap.run_id == "run-abc"


def test_artifact_payload_none_for_failure_result():
    """Failure ExecutionResult returns None from artifact_payload()."""
    r = ExecutionResult.from_executor_dict({"success": False, "error": "upstream"})
    assert r.artifact_payload() is None


def test_artifact_payload_none_fields_when_absent():
    """ArtifactPayload fields are None when keys absent from payload."""
    r = ExecutionResult(success=True, payload={"result_id": "r-x"})
    ap = r.artifact_payload()
    assert ap.result_id == "r-x"
    assert ap.output_path is None
    assert ap.rows_count is None
    assert ap.run_id is None


def test_artifact_payload_to_dict_omits_none():
    """ArtifactPayload.to_dict() only includes non-None fields."""
    ap = ArtifactPayload(result_id="r-1", rows_count=10)
    d = ap.to_dict()
    assert d == {"result_id": "r-1", "rows_count": 10}
    assert "output_path" not in d
    assert "filename" not in d


def test_artifact_payload_from_payload_dict_round_trip():
    """from_payload_dict -> to_dict reconstructs only known keys."""
    d = {"result_id": "r-2", "format": "csv", "rows_count": 5, "unknown_key": "x"}
    ap = ArtifactPayload.from_payload_dict(d)
    out = ap.to_dict()
    assert out == {"result_id": "r-2", "format": "csv", "rows_count": 5}
    assert "unknown_key" not in out


def test_artifact_payload_extra_payload_keys_preserved_in_execution_result():
    """Extra keys beyond ArtifactPayload coverage remain in ExecutionResult.payload."""
    r = ExecutionResult.from_executor_dict({
        "success": True,
        "result_id": "r-3",
        "user_error": False,
        "error_node_id": "n-42",
    })
    ap = r.artifact_payload()
    assert ap.result_id == "r-3"
    assert r.payload["user_error"] is False
    assert r.payload["error_node_id"] == "n-42"


def test_artifact_payload_is_frozen():
    ap = ArtifactPayload(result_id="r-1")
    with pytest.raises((AttributeError, TypeError)):
        ap.result_id = "other"  # type: ignore[misc]


def test_orchestrator_artifact_payload_accessible_after_execute_pipeline():
    """After execute_pipeline, exec_result.artifact_payload() exposes typed fields.

    Integration: verifies the real caller path from orchestrator through
    PipelineArtifact.from_execution_result uses the typed boundary.
    """
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
    raw_executor_dict = {
        "success": True,
        "result_id": "typed-r-1",
        "rows_count": 7,
        "format": "csv",
    }
    svc.estrai_executor = MagicMock()
    svc.estrai_executor.execute.return_value = raw_executor_dict
    svc._find_all_carica_nodes = MagicMock(return_value=[])

    artifact = svc.execute_pipeline("proj-typed")

    assert isinstance(artifact, PipelineArtifact)
    assert artifact.result_id == "typed-r-1"
    assert artifact["rows_count"] == 7

    # Verify the typed payload is extractable from the same data
    exec_result = ExecutionResult.from_executor_dict(raw_executor_dict)
    ap = exec_result.artifact_payload()
    assert ap.result_id == "typed-r-1"
    assert ap.rows_count == 7
    assert ap.format == "csv"
