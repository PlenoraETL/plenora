"""IO convert loss/report is preserved as a declared, bounded manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.native_pipeline import MalformedStageOutput, run_native_pipeline
from tests2.native_pipeline._fakes import FakeToolchain, build_config, build_request


pytestmark = [pytest.mark.functional, pytest.mark.contract]


def test_declared_loss_entries_are_preserved(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        "io",
        stdout=json.dumps(
            {
                "loss": {
                    "declared": True,
                    "entries": [
                        {
                            "kind": "metadata_dropped",
                            "key": "plenora.contract_version",
                        },
                        {"kind": "type_widened", "column": "importo"},
                    ],
                }
            }
        ),
    )

    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    assert result.loss.declared is True
    assert [entry["kind"] for entry in result.loss.entries] == [
        "metadata_dropped",
        "type_widened",
    ]


def test_absent_loss_key_means_nothing_declared(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("io", stdout=json.dumps({"converted": True}))

    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    assert result.loss.declared is False
    assert result.loss.entries == ()


def test_loss_list_shorthand_is_accepted_as_declared(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        "io", stdout=json.dumps({"loss": [{"kind": "srid_dropped"}]})
    )

    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    assert result.loss.declared is True
    assert result.loss.entries[0]["kind"] == "srid_dropped"


def test_unexpected_loss_shape_is_a_typed_error(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("io", stdout=json.dumps({"loss": "some columns were dropped"}))
    request = build_request(workdir)

    with pytest.raises(MalformedStageOutput) as excinfo:
        run_native_pipeline(request, build_config(fakes))

    assert excinfo.value.code == "malformed_stage_output"
    assert not request.output_path.exists()


def test_non_object_stdout_report_is_a_typed_error(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("io", stdout=json.dumps([1, 2, 3]))
    request = build_request(workdir)

    with pytest.raises(MalformedStageOutput):
        run_native_pipeline(request, build_config(fakes))

    assert not request.output_path.exists()


def test_invalid_json_report_is_a_typed_error(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("io", stdout="not json at all")
    request = build_request(workdir)

    with pytest.raises(MalformedStageOutput):
        run_native_pipeline(request, build_config(fakes))

    assert not request.output_path.exists()


def test_empty_stdout_report_is_accepted(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("io", stdout="")

    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    assert result.stages[-1].report is None
    assert result.loss.declared is False


def test_report_preceded_by_log_lines_is_still_parsed(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    """Bounded tolerance: the trailing line may carry the JSON document."""
    fakes.configure(
        "io",
        stdout="opening input\nconverting\n" + json.dumps({"loss": {"declared": False}}),
    )

    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    assert result.stages[-1].report is not None
    assert result.loss.declared is False


def test_oversized_report_is_rejected(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    padding = "z" * 4_000
    fakes.configure("io", stdout=json.dumps({"note": padding}))
    request = build_request(workdir)

    with pytest.raises(MalformedStageOutput):
        run_native_pipeline(
            request, build_config(fakes, max_report_bytes=1_024)
        )

    assert not request.output_path.exists()
