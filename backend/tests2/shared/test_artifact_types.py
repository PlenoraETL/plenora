"""
Tests the artifact types behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""


import pytest

from app.core.artifacts.types import LatestProjectArtifactRef, MaterializedArtifact, RunArtifactRef


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# RunArtifactRef


def test_run_artifact_ref_requires_project_id():
    with pytest.raises(ValueError, match="project_id"):
        RunArtifactRef(project_id="", result_id="r1")


def test_run_artifact_ref_requires_at_least_one_artifact():
    with pytest.raises(ValueError, match="senza artefatto"):
        RunArtifactRef(project_id="proj-1")


def test_run_artifact_ref_accepts_result_id_only():
    ref = RunArtifactRef(project_id="proj-1", result_id="res-1")
    assert ref.result_id == "res-1"
    assert ref.output_path is None


def test_run_artifact_ref_accepts_output_path_only():
    ref = RunArtifactRef(project_id="proj-1", output_path="/tmp/out.csv")
    assert ref.output_path == "/tmp/out.csv"


def test_run_artifact_ref_accepts_inline_df_only():
    ref = RunArtifactRef(project_id="proj-1", inline_df={"sentinel": True})
    assert ref.inline_df == {"sentinel": True}


def test_run_artifact_ref_is_frozen():
    ref = RunArtifactRef(project_id="proj-1", result_id="res-1")
    with pytest.raises(Exception):
        ref.result_id = "res-2"  # type: ignore[misc]


# LatestProjectArtifactRef


def test_latest_project_ref_requires_project_id():
    with pytest.raises(ValueError, match="project_id"):
        LatestProjectArtifactRef(project_id="")


# MaterializedArtifact


def test_materialized_artifact_requires_path_or_df():
    with pytest.raises(ValueError, match="vuoto"):
        MaterializedArtifact()


def test_materialized_artifact_cleanup_removes_temp_file(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("id\n1\n", encoding="utf-8")
    m = MaterializedArtifact(path=f, format="csv", is_temporary=True)
    m.cleanup()
    assert not f.exists()


def test_materialized_artifact_cleanup_skips_non_temporary(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("id\n1\n", encoding="utf-8")
    m = MaterializedArtifact(path=f, format="csv", is_temporary=False)
    m.cleanup()
    assert f.exists()


def test_materialized_artifact_context_manager_cleans_up(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("id\n1\n", encoding="utf-8")
    with MaterializedArtifact(path=f, format="csv", is_temporary=True) as m:
        assert m.path.exists()
    assert not f.exists()


def test_materialized_artifact_cleanup_idempotent(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("id\n1\n", encoding="utf-8")
    m = MaterializedArtifact(path=f, format="csv", is_temporary=True)
    m.cleanup()
    m.cleanup()  # non deve sollevare
