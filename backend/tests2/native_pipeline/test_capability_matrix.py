"""Capability gating happens before any subprocess is spawned."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.native_pipeline import (
    NativeCapability,
    PipelineOperation,
    SourceKind,
    UnsupportedCapability,
    capability_matrix,
    resolve_capability,
    run_native_pipeline,
)
from tests2.native_pipeline._fakes import FakeToolchain, build_config, build_request


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.security]


def test_matrix_declares_only_the_three_qualified_read_capabilities() -> None:
    matrix = capability_matrix()
    supported = {name for name, support in matrix.items() if support.supported}

    assert supported == {
        NativeCapability.POSTGRES_READ,
        NativeCapability.DATA_PLAN_APPLY,
        NativeCapability.IO_CONVERT,
    }


def test_every_capability_has_a_reason() -> None:
    for capability, support in capability_matrix().items():
        assert support.reason.strip(), f"{capability} has no documented reason"


@pytest.mark.parametrize(
    ("source_kind", "operation", "expected"),
    [
        (SourceKind.POSTGRES, PipelineOperation.READ, NativeCapability.POSTGRES_READ),
        (SourceKind.MYSQL, PipelineOperation.READ, NativeCapability.MYSQL_READ),
        (SourceKind.POSTGRES, PipelineOperation.WRITE, NativeCapability.POSTGRES_WRITE),
        (SourceKind.MYSQL, PipelineOperation.WRITE, NativeCapability.MYSQL_WRITE),
    ],
)
def test_resolve_capability_is_total_over_kind_and_operation(
    source_kind: SourceKind,
    operation: PipelineOperation,
    expected: NativeCapability,
) -> None:
    assert resolve_capability(source_kind, operation) is expected


@pytest.mark.parametrize(
    ("source_kind", "operation"),
    [
        (SourceKind.MYSQL, PipelineOperation.READ),
        (SourceKind.POSTGRES, PipelineOperation.WRITE),
        (SourceKind.MYSQL, PipelineOperation.WRITE),
        (SourceKind.SQLSERVER, PipelineOperation.READ),
        (SourceKind.SQLSERVER, PipelineOperation.WRITE),
    ],
)
def test_unsupported_capability_never_spawns_a_process(
    fakes: FakeToolchain,
    workdir: Path,
    dsn_env: str,
    source_kind: SourceKind,
    operation: PipelineOperation,
) -> None:
    request = build_request(workdir, source_kind=source_kind, operation=operation)

    with pytest.raises(UnsupportedCapability) as excinfo:
        run_native_pipeline(request, build_config(fakes))

    assert fakes.invocations() == []
    assert not fakes.log_path.exists()
    assert not request.output_path.exists()
    assert excinfo.value.code == "unsupported_capability"
    assert excinfo.value.capability is resolve_capability(source_kind, operation)


def test_unsupported_capability_is_not_a_fallback(
    fakes: FakeToolchain,
    workdir: Path,
    dsn_env: str,
) -> None:
    """MySQL read must fail, never silently degrade to the legacy path."""
    request = build_request(workdir, source_kind=SourceKind.MYSQL)

    with pytest.raises(UnsupportedCapability) as excinfo:
        run_native_pipeline(request, build_config(fakes))

    assert "mysql" in str(excinfo.value).lower()
    assert not request.output_path.exists()
