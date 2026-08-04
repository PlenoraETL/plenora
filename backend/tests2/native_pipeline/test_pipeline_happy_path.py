"""End-to-end PostgreSQL read -> Data plan -> IO convert against real fakes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.native_pipeline import (
    NativeCapability,
    PipelineStage,
    run_native_pipeline,
)
from tests2.native_pipeline._fakes import (
    DATA_PAYLOAD,
    DATA_VERSION,
    DATABASE_PAYLOAD,
    DATABASE_VERSION,
    DSN_ENV_VAR,
    IO_PAYLOAD,
    IO_VERSION,
    FakeToolchain,
    build_config,
    build_request,
)


pytestmark = [pytest.mark.functional, pytest.mark.contract]


def test_pipeline_publishes_the_io_artifact(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)

    result = run_native_pipeline(request, build_config(fakes))

    assert result.artifact_path == request.output_path
    assert request.output_path.read_bytes() == IO_PAYLOAD.encode("utf-8")
    assert result.artifact_bytes == len(IO_PAYLOAD.encode("utf-8"))
    assert result.capability is NativeCapability.POSTGRES_READ


def test_pipeline_reports_every_stage_in_order(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    assert [stage.stage for stage in result.stages] == [
        PipelineStage.DATABASE_READ,
        PipelineStage.DATA_PLAN,
        PipelineStage.IO_CONVERT,
    ]
    for stage in result.stages:
        assert stage.exit_code == 0
        assert stage.duration_ms >= 0


def test_pipeline_pins_the_observed_toolchain_versions(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    assert dict(result.toolchain) == {
        "database": DATABASE_VERSION,
        "data": DATA_VERSION,
        "io": IO_VERSION,
    }


def test_stage_argv_matches_the_pinned_cli_contracts(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)
    config = build_config(fakes)

    run_native_pipeline(request, config)

    stages = fakes.stage_invocations()
    assert [entry["tool"] for entry in stages] == ["database", "data", "io"]

    database_args = list(stages[0]["args"])
    assert database_args[0] == "postgres-read-ipc"
    assert database_args[1] == DSN_ENV_VAR
    assert database_args[2:4] == ["public", "misure"]
    assert "--max-rows" in database_args
    assert database_args[database_args.index("--max-rows") + 1] == "10000"
    assert "--max-output-bytes" in database_args
    assert "--timeout-ms" in database_args
    assert database_args[database_args.index("--order-by") + 1] == "id"

    data_args = list(stages[1]["args"])
    assert data_args[0] == "run"
    staged_plan = data_args[data_args.index("--plan") + 1]
    assert staged_plan != str(request.plan_path)
    assert staged_plan.endswith("00-plan.json")
    assert "plenora-native-" in staged_plan
    assert data_args[data_args.index("--input") + 1].endswith(".arrow")
    assert data_args[data_args.index("--output") + 1].endswith(".arrow")

    io_args = list(stages[2]["args"])
    assert io_args[0] == "convert"
    assert len(io_args) == 3


def test_each_stage_consumes_the_previous_stage_artifact(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    """The chain is a real file handoff: no dataframe crosses the boundary."""
    fakes.configure(
        "data",
        stdout=json.dumps({"accepted_rows": 3, "diagnostics": []}),
    )

    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    stages = fakes.stage_invocations()
    database_output = Path(stages[0]["args"][4])
    data_args = list(stages[1]["args"])
    io_args = list(stages[2]["args"])

    assert data_args[data_args.index("--input") + 1] == str(database_output)
    assert io_args[1] == data_args[data_args.index("--output") + 1]
    assert not hasattr(result, "dataframe")
    assert DATABASE_PAYLOAD != DATA_PAYLOAD


def test_optional_order_by_is_omitted_when_unset(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    run_native_pipeline(
        build_request(workdir, order_by=None), build_config(fakes)
    )

    database_args = list(fakes.stage_invocations()[0]["args"])
    assert "--order-by" not in database_args


def test_stage_reports_carry_the_parsed_tool_report(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    database_report = result.stages[0].report
    assert database_report is not None
    assert database_report["rows"] == 3


def test_pipeline_result_is_json_serialisable(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    payload = json.dumps(result.to_json_mapping())

    assert json.loads(payload)["capability"] == NativeCapability.POSTGRES_READ.value
