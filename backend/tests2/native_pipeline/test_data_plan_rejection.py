"""Data Tools exit 2 is a structured rejection, never a partial publication."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.native_pipeline import (
    DataPlanRejected,
    MalformedStageOutput,
    run_native_pipeline,
)
from tests2.native_pipeline._fakes import FakeToolchain, build_config, build_request


pytestmark = [pytest.mark.functional, pytest.mark.contract]


_DIAGNOSTICS = {
    "diagnostics": [
        {
            "code": "conversion.invalid_date",
            "source_index": 4,
            "column": "data_misura",
        },
        {
            "code": "conversion.invalid_date",
            "source_index": 1004,
            "column": "data_misura",
        },
    ],
    "accepted_rows": 0,
}


def test_exit_two_raises_a_typed_rejection_with_diagnostics(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        "data", exit_code=2, output_content=None, stdout=json.dumps(_DIAGNOSTICS)
    )
    request = build_request(workdir)

    with pytest.raises(DataPlanRejected) as excinfo:
        run_native_pipeline(request, build_config(fakes))

    rejection = excinfo.value
    assert rejection.code == "data_plan_rejected"
    assert rejection.exit_code == 2
    assert [entry["source_index"] for entry in rejection.diagnostics] == [4, 1004]
    assert rejection.diagnostics[0]["code"] == "conversion.invalid_date"


def test_exit_two_publishes_nothing_and_skips_the_io_stage(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        "data", exit_code=2, output_content=None, stdout=json.dumps(_DIAGNOSTICS)
    )
    request = build_request(workdir)

    with pytest.raises(DataPlanRejected):
        run_native_pipeline(request, build_config(fakes))

    assert not request.output_path.exists()
    assert [entry["tool"] for entry in fakes.stage_invocations()] == [
        "database",
        "data",
    ]


def test_real_data_error_envelope_preserves_row_diagnostics(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    row_diagnostics = {
        "contract": "plenora-row-diagnostics-v1",
        "scope": "read",
        "completeness": "complete",
        "index_basis": "source_row_zero_based",
        "total": 2,
        "observed_total": 2,
        "counts": {"conversion.invalid_date": 2},
        "examples": [
            {"source_index": 4, "cause": "conversion.invalid_date"},
            {"source_index": 1004, "cause": "conversion.invalid_date"},
        ],
    }
    fakes.configure(
        "data",
        exit_code=2,
        output_content=None,
        stdout="",
        stderr=json.dumps(
            {"status": "error", "error": {"row_diagnostics": row_diagnostics}}
        ),
    )

    with pytest.raises(DataPlanRejected) as excinfo:
        run_native_pipeline(build_request(workdir), build_config(fakes))

    assert excinfo.value.diagnostics == (row_diagnostics,)
    assert not build_request(workdir).output_path.exists()


def test_exit_two_diagnostics_may_arrive_on_stderr(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        "data",
        exit_code=2,
        output_content=None,
        stdout="",
        stderr=json.dumps(_DIAGNOSTICS),
    )

    with pytest.raises(DataPlanRejected) as excinfo:
        run_native_pipeline(build_request(workdir), build_config(fakes))

    assert [entry["source_index"] for entry in excinfo.value.diagnostics] == [4, 1004]


def test_exit_two_without_diagnostics_is_still_a_rejection(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        "data", exit_code=2, output_content=None, stdout="", stderr="plan rejected"
    )
    request = build_request(workdir)

    with pytest.raises(DataPlanRejected) as excinfo:
        run_native_pipeline(request, build_config(fakes))

    assert excinfo.value.diagnostics == ()
    assert not request.output_path.exists()


def test_exit_two_output_file_is_never_promoted(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    """A rejected plan that still wrote bytes must not reach the caller."""
    fakes.configure(
        "data",
        exit_code=2,
        output_content="ARROW-IPC-FAKE:rejected",
        stdout=json.dumps(_DIAGNOSTICS),
    )
    request = build_request(workdir)

    with pytest.raises(DataPlanRejected):
        run_native_pipeline(request, build_config(fakes))

    assert not request.output_path.exists()
    assert sorted(path.name for path in workdir.iterdir()) == ["plan.json"]


def test_non_object_diagnostics_shape_is_a_typed_error(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        "data",
        exit_code=0,
        stdout=json.dumps({"diagnostics": "two rows were invalid"}),
    )

    with pytest.raises(MalformedStageOutput):
        run_native_pipeline(build_request(workdir), build_config(fakes))


def test_successful_plan_diagnostics_reach_the_result(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        "data",
        stdout=json.dumps(
            {
                "accepted_rows": 3,
                "diagnostics": [{"code": "plan.note", "source_index": 1}],
            }
        ),
    )

    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    assert [entry["code"] for entry in result.diagnostics] == ["plan.note"]
