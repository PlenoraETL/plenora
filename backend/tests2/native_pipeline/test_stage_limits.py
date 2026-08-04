"""Stage timeout and bounded stdout/stderr are enforced by the runner."""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from app.core.native_pipeline import (
    StageStreamOverflow,
    StageTimeout,
    ToolchainIdentityError,
    run_native_pipeline,
)
from tests2.native_pipeline._fakes import FakeToolchain, build_config, build_request


pytestmark = [pytest.mark.functional, pytest.mark.security, pytest.mark.contract]


def test_stage_timeout_kills_the_child_and_publishes_nothing(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("database", sleep_ms=30_000)
    request = build_request(workdir)

    started = time.monotonic()
    with pytest.raises(StageTimeout) as excinfo:
        run_native_pipeline(request, build_config(fakes, stage_timeout_ms=750))
    elapsed = time.monotonic() - started

    assert excinfo.value.code == "stage_timeout"
    assert elapsed < 20.0, "the runner waited for the child instead of killing it"
    assert not request.output_path.exists()


def test_a_hanging_identity_probe_fails_closed_on_its_own_budget(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    """The probe budget is `identity_timeout_ms`, not the stage budget."""
    fakes.configure("database", version_sleep_ms=30_000)
    request = build_request(workdir)

    started = time.monotonic()
    with pytest.raises(ToolchainIdentityError) as excinfo:
        run_native_pipeline(
            request,
            build_config(fakes, identity_timeout_ms=750, stage_timeout_ms=60_000),
        )
    elapsed = time.monotonic() - started

    assert excinfo.value.details["reason"] == "probe_failed"
    assert elapsed < 20.0, "the probe waited on the stage budget"
    assert fakes.stage_invocations() == []
    assert not request.output_path.exists()


def test_a_slow_stage_does_not_disturb_identity_verification(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("data", sleep_ms=30_000)
    request = build_request(workdir)

    with pytest.raises(StageTimeout) as excinfo:
        run_native_pipeline(request, build_config(fakes, stage_timeout_ms=750))

    assert excinfo.value.stage.value == "data.plan"
    assert not request.output_path.exists()


def test_oversized_stdout_is_rejected(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("database", stdout_fill_bytes=512_000)
    request = build_request(workdir)

    with pytest.raises(StageStreamOverflow) as excinfo:
        run_native_pipeline(request, build_config(fakes, max_stdout_bytes=8_192))

    assert excinfo.value.code == "stage_stream_overflow"
    assert excinfo.value.stream == "stdout"
    assert not request.output_path.exists()


def test_oversized_stderr_is_rejected(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("data", stderr_fill_bytes=512_000)
    request = build_request(workdir)

    with pytest.raises(StageStreamOverflow) as excinfo:
        run_native_pipeline(request, build_config(fakes, max_stderr_bytes=8_192))

    assert excinfo.value.stream == "stderr"
    assert not request.output_path.exists()


def test_stream_bounds_do_not_trip_on_normal_output(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("io", stderr="conversion completed\n")

    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    assert result.stages[-1].stderr_bytes > 0
    assert "conversion completed" in result.stages[-1].stderr_tail
