"""
Tests the pipeline artifact behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

import pytest

from app.core.artifacts.types import (
    PipelineArtifact,
    RunArtifactRef,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# from_executor_result: classifier


def test_from_executor_result_inline_df_kind():
    sentinel = object()
    a = PipelineArtifact.from_executor_result(
        {"success": True, "dataframe": sentinel, "result_id": "res-1"},
        project_id="proj-1",
    )
    # inline batte result_id come kind: il consumer in process preferisce
    # il DataFrame in memoria che e' a tempo zero e non passa per il DB
    assert a.success is True
    assert a.artifact_kind == "inline"
    assert a.inline_df is sentinel


def test_from_executor_result_blob_kind():
    a = PipelineArtifact.from_executor_result(
        {
            "success": True,
            "result_id": "res-1",
            "output_path": "/tmp/legacy.csv",
            "format": "csv",
            "rows_count": 10,
        },
        project_id="proj-1",
    )
    assert a.artifact_kind == "blob"
    assert a.result_id == "res-1"
    assert a.format == "csv"
    assert a.rows_count == 10


def test_from_executor_result_file_kind():
    a = PipelineArtifact.from_executor_result(
        {"success": True, "output_path": "/tmp/x.csv"},
        project_id="proj-1",
    )
    assert a.artifact_kind == "file"
    assert a.output_path == "/tmp/x.csv"


def test_from_executor_result_none_kind_when_empty():
    a = PipelineArtifact.from_executor_result({}, project_id="proj-1")
    assert a.success is False
    assert a.artifact_kind == "none"


def test_from_executor_result_none_kind_when_blank_strings():
    a = PipelineArtifact.from_executor_result(
        {"success": True, "output_path": "", "result_id": ""},
        project_id="proj-1",
    )
    assert a.artifact_kind == "none"


def test_from_executor_result_failure_preserves_message():
    a = PipelineArtifact.from_executor_result(
        {"success": False, "message": "Pipeline upstream fallita"},
        project_id="proj-1",
    )
    assert a.success is False
    assert a.message == "Pipeline upstream fallita"
    assert a.artifact_kind == "none"


def test_from_executor_result_non_dict_input():
    a = PipelineArtifact.from_executor_result(None, project_id="proj-1")
    assert a.success is False
    assert a.artifact_kind == "none"


# Invariants


def test_pipeline_artifact_requires_project_id():
    with pytest.raises(ValueError, match="project_id"):
        PipelineArtifact({"success": True})


def test_pipeline_artifact_failure_helper_sets_message():
    a = PipelineArtifact.failure(project_id="proj-1", message="boom")
    assert a.success is False
    assert a.message == "boom"
    assert a.artifact_kind == "none"


# Dict-like back-compat (consumer legacy)


def test_pipeline_artifact_is_dict_subclass():
    a = PipelineArtifact({"project_id": "p1", "success": True})
    assert isinstance(a, dict)


def test_pipeline_artifact_supports_getitem_and_get():
    a = PipelineArtifact({"project_id": "p1", "success": True, "rows_count": 5})
    assert a["success"] is True
    assert a.get("rows_count") == 5
    assert a.get("missing", "default") == "default"


def test_pipeline_artifact_supports_setitem():
    """Service.execute_pipeline fa `result['carica'] = carica_info`:
    la mutazione deve continuare a funzionare."""
    a = PipelineArtifact({"project_id": "p1", "success": True})
    a["carica"] = {"tasks": [{"task_id": "t-1"}]}
    assert a["carica"]["tasks"][0]["task_id"] == "t-1"


def test_pipeline_artifact_supports_dict_spread():
    """workers/pipeline_task fa `**{k: v for k, v in result.items() ...}`:
    il dict spread deve enumerare le chiavi normalmente."""
    a = PipelineArtifact({
        "project_id": "p1",
        "success": True,
        "rows_count": 10,
        "carica": {"tasks": []},
    })
    spread = {**a}
    assert spread["project_id"] == "p1"
    assert spread["rows_count"] == 10
    assert spread["carica"] == {"tasks": []}


def test_pipeline_artifact_items_iteration():
    a = PipelineArtifact({"project_id": "p1", "success": True, "format": "csv"})
    keys = sorted(k for k, _ in a.items())
    assert "project_id" in keys
    assert "format" in keys


def test_pipeline_artifact_preserves_unknown_keys_from_executor():
    """Campi non normalizzati (error_node_id, user_error, timings, ...)
    devono restare accessibili come chiavi dict."""
    a = PipelineArtifact.from_executor_result(
        {
            "success": False,
            "message": "errore",
            "error_node_id": "n-1",
            "user_error": True,
            "timings": {"total": 1.5},
        },
        project_id="proj-1",
    )
    assert a.get("error_node_id") == "n-1"
    assert a.get("user_error") is True
    assert a["timings"] == {"total": 1.5}


def test_pipeline_artifact_from_executor_result_is_idempotent():
    """from_executor_result(PipelineArtifact) con stesso project_id
    deve tornare un PipelineArtifact equivalente, non doppio-wrap."""
    a = PipelineArtifact({"project_id": "p1", "success": True, "result_id": "r1"})
    b = PipelineArtifact.from_executor_result(a, project_id="p1")
    assert isinstance(b, PipelineArtifact)
    assert b.result_id == "r1"
    assert b.success is True


# Tenant binding: project_id passato dal caller e' la fonte autorevole


def test_from_executor_result_requires_project_id():
    with pytest.raises(ValueError, match="project_id obbligatorio"):
        PipelineArtifact.from_executor_result({"success": True}, project_id="")


def test_from_executor_result_raises_on_pid_mismatch_with_pipeline_artifact():
    """Caso piu' pericoloso: rewrap stale di un artefatto cross-progetto.
    Senza il check, to_run_artifact_ref() propagherebbe project_id
    sbagliato al resolver e potrebbe risolvere artefatti di un altro
    tenant (defense-in-depth oltre il check del resolver)."""
    stale = PipelineArtifact({
        "project_id": "proj-A",
        "success": True,
        "result_id": "res-1",
    })
    with pytest.raises(ValueError, match="project_id mismatch"):
        PipelineArtifact.from_executor_result(stale, project_id="proj-B")


def test_from_executor_result_raises_on_pid_mismatch_with_dict_raw():
    """Stessa policy applicata a dict raw: gli executor in genere non
    scrivono project_id, ma se lo fanno deve essere coerente con il
    tenant binding del caller."""
    raw = {"project_id": "proj-A", "success": True, "result_id": "res-1"}
    with pytest.raises(ValueError, match="project_id mismatch"):
        PipelineArtifact.from_executor_result(raw, project_id="proj-B")


def test_from_executor_result_overrides_blank_project_id_in_payload():
    """project_id assente o blank nel payload e' normale (gli executor
    in genere non lo scrivono): il binding del caller riempie il vuoto."""
    raw = {"success": True, "result_id": "res-1"}
    a = PipelineArtifact.from_executor_result(raw, project_id="proj-1")
    assert a.project_id == "proj-1"
    assert a.result_id == "res-1"


def test_from_executor_result_pipeline_artifact_with_matching_pid_is_ok():
    a = PipelineArtifact({
        "project_id": "proj-1",
        "success": True,
        "result_id": "res-1",
    })
    b = PipelineArtifact.from_executor_result(a, project_id="proj-1")
    assert b.project_id == "proj-1"
    assert b.result_id == "res-1"


# to_run_artifact_ref


def test_to_run_artifact_ref_preserves_blob_fields():
    a = PipelineArtifact({
        "project_id": "proj-1",
        "success": True,
        "result_id": "res-1",
        "output_path": "/tmp/legacy.csv",
    })
    ref = a.to_run_artifact_ref()
    assert isinstance(ref, RunArtifactRef)
    assert ref.project_id == "proj-1"
    assert ref.result_id == "res-1"
    assert ref.output_path == "/tmp/legacy.csv"


def test_to_run_artifact_ref_preserves_inline_df():
    sentinel = object()
    a = PipelineArtifact({
        "project_id": "proj-1",
        "success": True,
        "dataframe": sentinel,
    })
    ref = a.to_run_artifact_ref()
    assert ref.inline_df is sentinel


def test_to_run_artifact_ref_raises_for_none_kind():
    a = PipelineArtifact.failure(project_id="proj-1", message="vuoto")
    # Il Ref ha un __post_init__ che richiede almeno un artefatto:
    # convertire un PipelineArtifact "none" deve far fallire esplicitamente.
    with pytest.raises(ValueError, match="senza artefatto"):
        a.to_run_artifact_ref()
