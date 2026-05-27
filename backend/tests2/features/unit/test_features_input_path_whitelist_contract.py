"""
Tests the features input path whitelist behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from unittest.mock import patch

import pandas as pd
import pytest

from app.core.features.routes import _resolve_input_dataframe


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
        _resolve_input_dataframe(bad_path, project_id="", expected_group_id="grp-test")


def test_resolve_input_path_rejects_path_inside_former_whitelist(tmp_path):
    """Local filesystem input is removed, even for formerly whitelisted paths."""
    allowed_file = tmp_path / "results" / "proj-1" / "output.parquet"
    allowed_file.parent.mkdir(parents=True, exist_ok=True)
    allowed_file.write_text("ok", encoding="utf-8")

    with pytest.raises(ValueError, match="input_path locale disabilitato"):
        _resolve_input_dataframe(
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
        _resolve_input_dataframe("input", project_id="proj-1")  # type: ignore[call-arg]


def test_resolve_input_path_rejects_blank_expected_group_id():
    with pytest.raises(ValueError, match="expected_group_id obbligatorio"):
        _resolve_input_dataframe("", project_id="proj-1", expected_group_id="")
    with pytest.raises(ValueError, match="expected_group_id obbligatorio"):
        _resolve_input_dataframe("", project_id="proj-1", expected_group_id=None)  # type: ignore[arg-type]


def test_resolve_input_path_blocks_local_path_without_explicit_dev_flag(monkeypatch, tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    local_file = results_dir / "proj-1" / "output.csv"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_text("ok", encoding="utf-8")

    with pytest.raises(ValueError, match="input_path locale disabilitato"):
        _resolve_input_dataframe(
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
        _resolve_input_dataframe(
            str(local_file),
            project_id="",
            expected_group_id="grp-test",
        )


def test_resolve_input_path_project_branch_propagates_expected_group_id(tmp_path):
    from app.core.artifacts.types import MaterializedArtifact

    fake_df = pd.DataFrame({"id": [1]})
    materialized = MaterializedArtifact(df=fake_df, format="parquet")

    with patch(
        "app.core.features.routes.ArtifactResolver.materialize_latest",
        return_value=materialized,
    ) as materialize_latest:
        resolved_df, resolved_format = _resolve_input_dataframe(
            "",
            project_id="proj-1",
            expected_group_id="grp-test",
        )

    assert resolved_df is fake_df
    assert resolved_format == "parquet"
    materialize_latest.assert_called_once()
    _, kwargs = materialize_latest.call_args
    assert kwargs["expected_group_id"] == "grp-test"
    assert kwargs["mode"] == "bytes"


def test_resolve_input_path_project_branch_loads_bytes_without_tempfile(tmp_path):
    """Post-Phase5: project_id usa bytes/df in memoria, non tempfile."""
    from app.core.artifacts.types import MaterializedArtifact

    materialized = MaterializedArtifact(data=b"raw", format="csv")
    loaded_df = pd.DataFrame({"id": [1]})

    with (
        patch(
            "app.core.features.routes.ArtifactResolver.materialize_latest",
            return_value=materialized,
        ),
        patch(
            "app.core.dataframes.frame_io.load_dataframe_from_artifact",
            return_value=loaded_df,
        ) as load_dataframe_from_artifact,
    ):
        resolved_df, resolved_format = _resolve_input_dataframe(
            "",
            project_id="proj-1",
            expected_group_id="grp-test",
        )

    assert resolved_df is loaded_df
    assert resolved_format == "csv"
    load_dataframe_from_artifact.assert_called_once_with(
        data=b"raw", format_hint="csv"
    )


def test_resolve_input_path_project_branch_raises_when_no_output_found():
    with patch(
        "app.core.features.routes.ArtifactResolver.materialize_latest",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError, match="Nessun output trovato"):
            _resolve_input_dataframe(
                "",
                project_id="proj-1",
                expected_group_id="grp-test",
            )
