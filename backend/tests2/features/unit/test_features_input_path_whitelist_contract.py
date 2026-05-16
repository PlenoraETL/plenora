"""
Tests the features input path whitelist behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from unittest.mock import patch

import pytest

from app.core.features.routes import _resolve_input_path


pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd",
        "/proc/self/environ",
        "../../../../etc/shadow",
        "C:/Windows/System32/config/SAM",
        "\\\\server\\share\\secret.txt",
    ],
)
def test_resolve_input_path_rejects_paths_outside_whitelist(monkeypatch, tmp_path, bad_path):
    with pytest.raises(ValueError, match="input_path locale disabilitato"):
        _resolve_input_path(bad_path, project_id="", expected_group_id="grp-test")


def test_resolve_input_path_rejects_path_inside_former_whitelist(tmp_path):
    """Local filesystem input is removed, even for formerly whitelisted paths."""
    allowed_file = tmp_path / "results" / "proj-1" / "output.parquet"
    allowed_file.parent.mkdir(parents=True, exist_ok=True)
    allowed_file.write_text("ok", encoding="utf-8")

    with pytest.raises(ValueError, match="input_path locale disabilitato"):
        _resolve_input_path(
            str(allowed_file),
            project_id="",
            expected_group_id="grp-test",
        )


# Refactor #2 cleanup: expected_group_id keyword-only required


def test_resolve_input_path_requires_expected_group_id_keyword():
    """Pre-cleanup expected_group_id era opzionale (default None) e
    chi dimenticava di passarlo bypassava silenziosamente il check
    cross-tenant del resolver. Ora keyword-only required: ometterlo
    deve sollevare TypeError."""
    with pytest.raises(TypeError):
        _resolve_input_path("input", project_id="proj-1")  # type: ignore[call-arg]


def test_resolve_input_path_rejects_blank_expected_group_id():
    with pytest.raises(ValueError, match="expected_group_id obbligatorio"):
        _resolve_input_path("", project_id="proj-1", expected_group_id="")
    with pytest.raises(ValueError, match="expected_group_id obbligatorio"):
        _resolve_input_path("", project_id="proj-1", expected_group_id=None)  # type: ignore[arg-type]


def test_resolve_input_path_blocks_local_path_without_explicit_dev_flag(monkeypatch, tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    local_file = results_dir / "proj-1" / "output.csv"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_text("ok", encoding="utf-8")

    with pytest.raises(ValueError, match="input_path locale disabilitato"):
        _resolve_input_path(
            str(local_file),
            project_id="",
            expected_group_id="grp-test",
        )


@pytest.mark.parametrize("env_name", ["production", "prod", "staging", "stage"])
def test_resolve_input_path_blocks_local_path_in_hardened_env_even_with_flag(
    monkeypatch, tmp_path, env_name
):
    local_file = tmp_path / "results" / "proj-1" / "output.csv"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_text("ok", encoding="utf-8")

    monkeypatch.setenv("ENVIRONMENT", env_name)

    with pytest.raises(ValueError, match="input_path locale disabilitato"):
        _resolve_input_path(
            str(local_file),
            project_id="",
            expected_group_id="grp-test",
        )


def test_resolve_input_path_project_branch_propagates_expected_group_id(tmp_path):
    from app.core.artifacts.types import MaterializedArtifact

    # Path concreto cross-platform (evita '/tmp/...' che su Windows
    # diventa '\\tmp\\...' e fa fallire l'assertion stringa).
    fake_output = tmp_path / "output.parquet"
    fake_output.write_text("ok", encoding="utf-8")
    materialized = MaterializedArtifact(path=fake_output, is_temporary=False)

    with patch(
        "app.core.features.routes.ArtifactResolver.materialize_latest",
        return_value=materialized,
    ) as materialize_latest:
        resolved = _resolve_input_path(
            "",
            project_id="proj-1",
            expected_group_id="grp-test",
        )

    assert resolved.path == fake_output
    materialize_latest.assert_called_once()
    _, kwargs = materialize_latest.call_args
    assert kwargs["expected_group_id"] == "grp-test"


def test_resolve_input_path_project_branch_cleans_up_temp_blob(tmp_path):
    """Regression guard per il leak: write_features / create_service con
    project_id devono pulire il tempfile materializzato dal blob DB.
    Se `_resolve_input_path` riperde il riferimento al MaterializedArtifact
    (restituendo str(path)), il cleanup non viene mai invocato."""
    from app.core.artifacts.types import MaterializedArtifact

    temp_blob = tmp_path / "temp_blob.parquet"
    temp_blob.write_text("blob", encoding="utf-8")
    materialized = MaterializedArtifact(
        path=temp_blob, is_temporary=True,
    )

    with patch(
        "app.core.features.routes.ArtifactResolver.materialize_latest",
        return_value=materialized,
    ):
        with _resolve_input_path(
            "",
            project_id="proj-1",
            expected_group_id="grp-test",
        ) as resolved:
            assert resolved.path == temp_blob
            assert temp_blob.exists()

    assert not temp_blob.exists(), (
        "tempfile non pulito: _resolve_input_path deve ritornare un "
        "MaterializedArtifact context-managed"
    )


def test_resolve_input_path_project_branch_raises_when_no_output_found():
    with patch(
        "app.core.features.routes.ArtifactResolver.materialize_latest",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError, match="Nessun output trovato"):
            _resolve_input_path(
                "",
                project_id="proj-1",
                expected_group_id="grp-test",
            )
