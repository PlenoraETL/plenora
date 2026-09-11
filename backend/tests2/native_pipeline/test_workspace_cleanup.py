"""The isolated stage workspace is removed on success and on failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.native_pipeline import (
    DataPlanRejected,
    StageFailed,
    StageTimeout,
    run_native_pipeline,
)
from tests2.native_pipeline._fakes import FakeToolchain, build_config, build_request


pytestmark = [pytest.mark.functional, pytest.mark.contract]


def _workspaces(root: Path) -> list[Path]:
    return [path for path in root.iterdir() if path.name.startswith("plenora-native-")]


def test_workspace_is_removed_after_success(
    fakes: FakeToolchain, workdir: Path, dsn_env: str, tmp_path: Path
) -> None:
    root = tmp_path / "scratch"
    root.mkdir()

    run_native_pipeline(
        build_request(workdir), build_config(fakes, workspace_root=root)
    )

    assert _workspaces(root) == []


def test_workspace_is_removed_after_stage_failure(
    fakes: FakeToolchain, workdir: Path, dsn_env: str, tmp_path: Path
) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    fakes.configure("data", exit_code=4, output_content=None)

    with pytest.raises(StageFailed):
        run_native_pipeline(
            build_request(workdir), build_config(fakes, workspace_root=root)
        )

    assert _workspaces(root) == []


def test_workspace_is_removed_after_plan_rejection(
    fakes: FakeToolchain, workdir: Path, dsn_env: str, tmp_path: Path
) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    fakes.configure("data", exit_code=2, output_content=None, stdout="{}")

    with pytest.raises(DataPlanRejected):
        run_native_pipeline(
            build_request(workdir), build_config(fakes, workspace_root=root)
        )

    assert _workspaces(root) == []


def test_workspace_is_removed_after_timeout(
    fakes: FakeToolchain, workdir: Path, dsn_env: str, tmp_path: Path
) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    fakes.configure("database", sleep_ms=30_000)

    with pytest.raises(StageTimeout):
        run_native_pipeline(
            build_request(workdir),
            build_config(fakes, workspace_root=root, stage_timeout_ms=750),
        )

    assert _workspaces(root) == []


def test_stage_files_live_inside_the_workspace(
    fakes: FakeToolchain, workdir: Path, dsn_env: str, tmp_path: Path
) -> None:
    root = tmp_path / "scratch"
    root.mkdir()

    run_native_pipeline(
        build_request(workdir), build_config(fakes, workspace_root=root)
    )

    database_output = Path(fakes.stage_invocations()[0]["args"][4])
    assert root in database_output.parents
    assert not database_output.exists()
