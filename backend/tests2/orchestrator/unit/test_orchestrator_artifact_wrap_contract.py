"""Contract test — OrchestratorService wrap di executor result in PipelineArtifact.

Il contratto centrale: `execute_pipeline` chiama l'executor specifico
(estrai/manipola/arricchisci), riceve un dict raw, poi lo wrappa in
PipelineArtifact via `from_executor_result(result, project_id=...)`.

Questo test verifica il contratto di traduzione:
  - artifact_kind in {'inline', 'blob', 'file', 'none'} derivato corretto
  - result_id / output_path / inline_df propagati
  - project_id binding autorevole (override discreto / fail-closed su mismatch)
  - dict-like access (artifact["success"], artifact["result_id"]) OK
  - to_run_artifact_ref() produce Ref risolvibile per kind != 'none'

Locka invariante cruciale che altrimenti si potrebbe rompere in modi
silenziosi durante refactor di piping (es. cambio shape dict executor).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.artifacts.types import (
    PipelineArtifact,
    ArtifactResolutionError,
    RunArtifactRef,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# =============================================================================
# 1. artifact_kind derivation
# =============================================================================


def test_kind_blob_when_result_id_present():
    a = PipelineArtifact.from_executor_result(
        {"success": True, "result_id": "res-abc", "rows_count": 10},
        project_id="p1",
    )
    assert a.artifact_kind == "blob"
    assert a.result_id == "res-abc"
    assert a.project_id == "p1"


def test_kind_file_when_only_output_path():
    a = PipelineArtifact.from_executor_result(
        {"success": True, "output_path": "/tmp/result.csv"},
        project_id="p1",
    )
    assert a.artifact_kind == "file"
    assert a.output_path == "/tmp/result.csv"
    assert a.result_id is None


def test_kind_inline_when_dataframe_present():
    import pandas as pd
    df = pd.DataFrame({"a": [1, 2]})
    a = PipelineArtifact.from_executor_result(
        {"success": True, "dataframe": df},
        project_id="p1",
    )
    assert a.artifact_kind == "inline"
    assert a.inline_df is df


def test_kind_none_when_no_artifact_refs():
    a = PipelineArtifact.from_executor_result(
        {"success": True, "message": "ok ma nessun artefatto"},
        project_id="p1",
    )
    assert a.artifact_kind == "none"


def test_kind_priority_inline_over_blob_over_file():
    """Se piu' campi sono presenti, priority inline > blob > file."""
    import pandas as pd
    df = pd.DataFrame({"a": [1]})
    a = PipelineArtifact.from_executor_result(
        {"dataframe": df, "result_id": "res-1", "output_path": "/t/x"},
        project_id="p1",
    )
    assert a.artifact_kind == "inline"

    b = PipelineArtifact.from_executor_result(
        {"result_id": "res-1", "output_path": "/t/x"},
        project_id="p1",
    )
    assert b.artifact_kind == "blob"


# =============================================================================
# 2. project_id binding autorevole
# =============================================================================


def test_project_id_overrides_when_missing_in_payload():
    """Payload senza project_id → usa il param keyword."""
    a = PipelineArtifact.from_executor_result(
        {"success": True, "result_id": "r1"},
        project_id="p-authoritative",
    )
    assert a.project_id == "p-authoritative"


def test_project_id_mismatch_raises_fail_closed():
    """Se il payload ha project_id diverso → ValueError (no silent cross-tenant)."""
    with pytest.raises(ValueError, match=r"project_id mismatch"):
        PipelineArtifact.from_executor_result(
            {"result_id": "r1", "project_id": "other-tenant"},
            project_id="p1",
        )


def test_project_id_empty_keyword_raises():
    with pytest.raises(ValueError, match=r"project_id"):
        PipelineArtifact.from_executor_result(
            {"result_id": "r1"}, project_id="",
        )


# =============================================================================
# 3. Input non-dict → artifact 'none' safe
# =============================================================================


def test_non_dict_input_produces_none_artifact_not_raise():
    a = PipelineArtifact.from_executor_result(None, project_id="p1")
    assert a.artifact_kind == "none"
    assert a["success"] is False
    assert a.project_id == "p1"

    b = PipelineArtifact.from_executor_result("string result", project_id="p1")
    assert b.artifact_kind == "none"


# =============================================================================
# 4. Dict-like access per retrocompat legacy consumer
# =============================================================================


def test_artifact_is_dict_subclass_for_legacy_consumers():
    a = PipelineArtifact.from_executor_result(
        {"success": True, "result_id": "r1", "custom_field": "foo"},
        project_id="p1",
    )
    # Consumer legacy usano artifact["success"], artifact["result_id"], ecc.
    assert a["success"] is True
    assert a["result_id"] == "r1"
    assert a["custom_field"] == "foo"
    # Mutability per compatibilita' (assegnazione chiavi funziona)
    a["carica"] = {"task_id": "ct-1"}
    assert a["carica"] == {"task_id": "ct-1"}


# =============================================================================
# 5. to_run_artifact_ref solleva su kind='none', altrimenti Ref valido
# =============================================================================


def test_to_run_artifact_ref_for_blob():
    a = PipelineArtifact.from_executor_result(
        {"result_id": "r1"}, project_id="p1",
    )
    ref = a.to_run_artifact_ref()
    assert isinstance(ref, RunArtifactRef)
    assert ref.project_id == "p1"
    assert ref.result_id == "r1"
    assert ref.output_path is None
    assert ref.inline_df is None


def test_to_run_artifact_ref_for_file():
    a = PipelineArtifact.from_executor_result(
        {"output_path": "/tmp/r.csv"}, project_id="p1",
    )
    ref = a.to_run_artifact_ref()
    assert ref.output_path == "/tmp/r.csv"


def test_to_run_artifact_ref_none_raises():
    """Artifact 'none' non e' risolvibile → RunArtifactRef.__post_init__
    solleva."""
    a = PipelineArtifact.from_executor_result(
        {"success": True}, project_id="p1",
    )
    with pytest.raises(ValueError):
        a.to_run_artifact_ref()


# =============================================================================
# 6. Integrazione: orchestrator.execute_pipeline → PipelineArtifact
# =============================================================================


def test_orchestrator_wraps_estrai_executor_result_as_blob_artifact():
    """End-to-end sul path execute_pipeline: wrap corretto del dict
    ritornato da EstraiExecutor."""
    from app.core.orchestrator.service import OrchestratorService

    mock_project_service = MagicMock()
    mock_project_service.get_project_metadata.return_value = MagicMock(
        project_type="estrai"
    )
    mock_project_service.get_project_canvas.return_value = MagicMock()

    fake_estrai_result = {
        "success": True,
        "result_id": "res-est-123",
        "rows_count": 42,
        "output_filename": "estrai.csv",
    }

    svc = OrchestratorService(
        project_service=mock_project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
    )
    # Sostituisci l'executor-specifico con un mock che ritorna il dict noto
    svc.estrai_executor = MagicMock()
    svc.estrai_executor.execute = MagicMock(return_value=fake_estrai_result)

    # Niente CARICA → _find_all_carica_nodes ritorna []
    svc._find_all_carica_nodes = MagicMock(return_value=[])

    artifact = svc.execute_pipeline("p1")

    assert isinstance(artifact, PipelineArtifact)
    assert artifact.artifact_kind == "blob"
    assert artifact.result_id == "res-est-123"
    assert artifact.project_id == "p1"
    assert artifact.success is True
    # Legacy consumer access
    assert artifact["result_id"] == "res-est-123"
    assert artifact["rows_count"] == 42


def test_orchestrator_wraps_failure_result_as_none_artifact():
    """Executor fallito → artifact con success=False, kind=none."""
    from app.core.orchestrator.service import OrchestratorService

    mock_project_service = MagicMock()
    mock_project_service.get_project_metadata.return_value = MagicMock(
        project_type="manipola"
    )
    mock_project_service.get_project_canvas.return_value = MagicMock()

    fake_failure = {
        "success": False,
        "message": "Errore sorgente non raggiungibile",
    }

    svc = OrchestratorService(
        project_service=mock_project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
    )
    svc.manipola_executor = MagicMock()
    svc.manipola_executor.execute = MagicMock(return_value=fake_failure)
    svc._find_all_carica_nodes = MagicMock(return_value=[])

    artifact = svc.execute_pipeline("p1")

    assert isinstance(artifact, PipelineArtifact)
    assert artifact.artifact_kind == "none"
    assert artifact.success is False
    assert "sorgente" in artifact["message"].lower()


def test_orchestrator_preserves_carica_info_on_wrapped_artifact():
    """Se il pipeline triggerra un CARICA, il task_info viene aggiunto
    all'artifact come dict['carica'] — verificato post-wrap."""
    from app.core.orchestrator.service import OrchestratorService

    mock_project_service = MagicMock()
    mock_project_service.get_project_metadata.return_value = MagicMock(
        project_type="arricchisci"
    )
    mock_project_service.get_project_canvas.return_value = MagicMock()

    fake_result = {"success": True, "result_id": "res-enr-1"}

    svc = OrchestratorService(
        project_service=mock_project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
    )
    svc.enrich_executor = MagicMock()
    svc.enrich_executor.execute = MagicMock(return_value=fake_result)
    # Simula presenza di CARICA
    svc._execute_carica_if_present = MagicMock(return_value={
        "tasks": [{"task_id": "ct-1", "node_id": "carica-a"}],
        "message": "1 task CARICA avviati",
    })

    artifact = svc.execute_pipeline("p1")

    assert artifact.success is True
    assert artifact.artifact_kind == "blob"
    # Legacy consumer access: artifact["carica"] deve esistere post-wrap
    assert "carica" in artifact
    assert artifact["carica"]["tasks"][0]["task_id"] == "ct-1"
