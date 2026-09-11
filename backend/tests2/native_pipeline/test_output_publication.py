"""No-clobber preflight, atomic publication and failure-time non-publication."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.native_pipeline import (
    InvalidNativeRequest,
    OutputAlreadyExists,
    StageFailed,
    run_native_pipeline,
)
from tests2.native_pipeline._fakes import (
    IO_PAYLOAD,
    FakeToolchain,
    build_config,
    build_request,
)


pytestmark = [pytest.mark.functional, pytest.mark.security, pytest.mark.contract]


def test_existing_output_is_rejected_without_spawning(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)
    request.output_path.write_bytes(b"pre-existing artifact")

    with pytest.raises(OutputAlreadyExists) as excinfo:
        run_native_pipeline(request, build_config(fakes))

    assert excinfo.value.code == "output_already_exists"
    assert request.output_path.read_bytes() == b"pre-existing artifact"
    assert fakes.invocations() == []


def test_existing_output_directory_is_rejected(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)
    request.output_path.mkdir()

    with pytest.raises(OutputAlreadyExists):
        run_native_pipeline(request, build_config(fakes))

    assert fakes.invocations() == []


def test_missing_output_parent_is_rejected_without_spawning(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)
    absent = workdir / "missing-dir" / "final.arrow"
    request = type(request)(
        source=request.source,
        plan_path=request.plan_path,
        output_path=absent,
        source_kind=request.source_kind,
        operation=request.operation,
    )

    with pytest.raises(InvalidNativeRequest):
        run_native_pipeline(request, build_config(fakes))

    assert fakes.invocations() == []


def test_missing_plan_is_rejected_without_spawning(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)
    request.plan_path.unlink()

    with pytest.raises(InvalidNativeRequest):
        run_native_pipeline(request, build_config(fakes))

    assert fakes.invocations() == []


def test_missing_dsn_env_var_is_rejected_without_spawning(
    fakes: FakeToolchain, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PLENORA_NATIVE_TEST_DSN", raising=False)
    request = build_request(workdir)

    with pytest.raises(InvalidNativeRequest) as excinfo:
        run_native_pipeline(request, build_config(fakes))

    assert fakes.invocations() == []
    assert "PLENORA_NATIVE_TEST_DSN" in str(excinfo.value)


def test_failed_stage_publishes_nothing(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("io", exit_code=7, output_content=None, stdout="")
    request = build_request(workdir)

    with pytest.raises(StageFailed) as excinfo:
        run_native_pipeline(request, build_config(fakes))

    assert excinfo.value.exit_code == 7
    assert not request.output_path.exists()
    assert list(workdir.iterdir()) == [request.plan_path]


def test_stage_that_forgets_its_output_is_a_typed_failure(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("data", output_content=None)
    request = build_request(workdir)

    with pytest.raises(StageFailed) as excinfo:
        run_native_pipeline(request, build_config(fakes))

    assert excinfo.value.code == "stage_failed"
    assert not request.output_path.exists()


def test_publication_leaves_no_partial_files_behind(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)

    run_native_pipeline(request, build_config(fakes))

    published = sorted(path.name for path in workdir.iterdir())
    assert published == ["final.arrow", "plan.json"]
    assert request.output_path.read_bytes() == IO_PAYLOAD.encode("utf-8")
