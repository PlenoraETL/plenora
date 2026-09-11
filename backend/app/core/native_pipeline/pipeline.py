"""The explicit native path: PostgreSQL read -> Data plan -> IO convert.

This module is a parallel path, not a replacement: it never falls back to the
legacy pandas chain and the legacy chain never delegates to it. A run either
produces the artifact the pinned binaries produced, or raises a typed error
having published nothing.

Order of the gates, all of them before the first spawn:

1. the explicit opt-in must be on;
2. the capability must be supported by the pinned releases;
3. the request must be well formed and its inputs must exist;
4. the output path must be free;
5. the DSN environment variable must be set.

Only then are the three binaries probed for identity, and only then do stages
run.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from app.config import settings
from app.core.native_pipeline.capabilities import (
    capability_matrix,
    resolve_capability,
)
from app.core.native_pipeline.commands import (
    data_plan_argv,
    database_read_argv,
    io_convert_argv,
)
from app.core.native_pipeline.config import NativeToolchainConfig
from app.core.native_pipeline.errors import (
    DataPlanRejected,
    InvalidNativeRequest,
    MalformedStageOutput,
    NativePipelineDisabled,
    NativePlatformUnsupported,
    StageFailed,
    UnsupportedCapability,
)
from app.core.native_pipeline.identity import materialize_toolchain, verify_toolchain
from app.core.native_pipeline.models import (
    LossReport,
    NativePipelineRequest,
    NativePipelineResult,
    StageReport,
)
from app.core.native_pipeline.process import ProcessOutcome, run_bounded_process
from app.core.native_pipeline.publication import (
    PreparedPublication,
    assert_output_absent,
    prepare_artifact,
)
from app.core.native_pipeline.redaction import SecretRedactor
from app.core.native_pipeline.reports import (
    extract_diagnostics,
    extract_loss,
    merge_diagnostics,
    parse_stage_report,
    stderr_tail,
)
from app.core.native_pipeline.stages import PipelineStage
from app.core.native_pipeline.workspace import (
    Workspace,
    build_child_env,
    open_workspace,
)


# Environment variable names are constrained so a request cannot smuggle shell
# or argv syntax into the position where the DSN variable name is passed.
_ENV_VAR_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$", re.ASCII)

# Data Tools reserves exit 2 for a contractual plan rejection carrying
# row-scoped diagnostics. Any other non-zero exit is a stage failure.
_DATA_REJECTION_EXIT_CODE = 2

_DATABASE_OUTPUT = "01-database.arrow"
_DATA_OUTPUT = "02-data.arrow"
_IO_OUTPUT = "03-io.arrow"


def run_native_pipeline(
    request: NativePipelineRequest, config: NativeToolchainConfig
) -> NativePipelineResult:
    """Run the native chain and publish one artifact, or raise.

    Returns paths and reports only: no dataframe is ever materialised in the
    backend process.
    """
    if not config.enabled:
        raise NativePipelineDisabled(
            "the native pipeline is disabled; enable it explicitly with "
            "NATIVE_PIPELINE_ENABLED",
            details={"setting": "NATIVE_PIPELINE_ENABLED"},
        )

    capability = resolve_capability(request.source_kind, request.operation)
    support = capability_matrix()[capability]
    if not support.supported:
        raise UnsupportedCapability(capability, support.reason)

    if os.name != "posix" or not hasattr(os, "killpg"):
        raise NativePlatformUnsupported(
            "the native pipeline is Linux/POSIX-only because process-group "
            "termination is a required safety guarantee"
        )

    _validate_request(request, config)
    assert_output_absent(request.output_path)
    dsn_value = _read_dsn(request.source.dsn_env_var)

    redactor = SecretRedactor((dsn_value,))
    prepared: PreparedPublication | None = None
    try:
        with open_workspace(config) as workspace:
            plan_snapshot = _snapshot_plan(request.plan_path, workspace, config)
            snapshot_config = materialize_toolchain(config, workspace=workspace.root)
            probe_env = build_child_env(snapshot_config, scratch=workspace.scratch)
            toolchain = verify_toolchain(
                snapshot_config,
                env=probe_env,
                cwd=workspace.scratch,
                redactor=redactor,
            )

            stages, diagnostics, loss = _run_stages(
                replace(request, plan_path=plan_snapshot),
                snapshot_config,
                workspace=workspace,
                redactor=redactor,
                dsn_value=dsn_value,
            )
            prepared = prepare_artifact(
                workspace.stage_path(_IO_OUTPUT),
                request.output_path,
                max_bytes=config.max_stage_artifact_bytes,
            )
    except BaseException:
        # Safe secondary cleanup: only a cryptographically unique staged path
        # created by this run is ever removed. A competing final is untouched.
        if prepared is not None:
            with suppress(OSError):
                prepared.discard()
        raise

    assert prepared is not None
    artifact_bytes = prepared.commit()

    return NativePipelineResult(
        capability=capability,
        artifact_path=request.output_path,
        artifact_bytes=artifact_bytes,
        stages=stages,
        loss=loss,
        diagnostics=diagnostics,
        toolchain=toolchain,
    )


def _run_stages(
    request: NativePipelineRequest,
    config: NativeToolchainConfig,
    *,
    workspace: Workspace,
    redactor: SecretRedactor,
    dsn_value: str,
) -> tuple[tuple[StageReport, ...], tuple[Mapping[str, Any], ...], LossReport]:
    database_output = workspace.stage_path(_DATABASE_OUTPUT)
    data_output = workspace.stage_path(_DATA_OUTPUT)
    io_output = workspace.stage_path(_IO_OUTPUT)

    database_report = _run_stage(
        PipelineStage.DATABASE_READ,
        database_read_argv(config, request, database_output),
        config=config,
        workspace=workspace,
        redactor=redactor,
        expected_output=database_output,
        env=build_child_env(
            config,
            scratch=workspace.scratch,
            secret_env_var=request.source.dsn_env_var,
            secret_value=dsn_value,
        ),
    )

    data_report = _run_stage(
        PipelineStage.DATA_PLAN,
        data_plan_argv(
            config,
            plan_path=request.plan_path,
            input_path=database_output,
            output_path=data_output,
        ),
        config=config,
        workspace=workspace,
        redactor=redactor,
        expected_output=data_output,
        env=build_child_env(config, scratch=workspace.scratch),
    )

    io_report = _run_stage(
        PipelineStage.IO_CONVERT,
        io_convert_argv(config, input_path=data_output, output_path=io_output),
        config=config,
        workspace=workspace,
        redactor=redactor,
        expected_output=io_output,
        env=build_child_env(config, scratch=workspace.scratch),
    )

    stages = (database_report, data_report, io_report)
    diagnostics = merge_diagnostics(
        [
            extract_diagnostics(stage.report, stage=stage.stage)
            for stage in stages
        ]
    )
    loss = extract_loss(io_report.report, stage=PipelineStage.IO_CONVERT)
    return stages, diagnostics, loss


def _run_stage(
    stage: PipelineStage,
    argv: tuple[str, ...],
    *,
    config: NativeToolchainConfig,
    workspace: Workspace,
    redactor: SecretRedactor,
    expected_output: Path,
    env: Mapping[str, str],
) -> StageReport:
    outcome = run_bounded_process(
        argv,
        stage=stage,
        env=env,
        cwd=workspace.scratch,
        timeout_ms=config.stage_timeout_ms,
        max_stdout_bytes=config.max_stdout_bytes,
        max_stderr_bytes=config.max_stderr_bytes,
        redactor=redactor,
        watched_output_path=expected_output,
        max_output_bytes=config.max_stage_artifact_bytes,
    )
    tail = stderr_tail(outcome.stderr, redactor)

    if stage is PipelineStage.DATA_PLAN and outcome.exit_code == (
        _DATA_REJECTION_EXIT_CODE
    ):
        raise _plan_rejection(outcome, config=config, redactor=redactor, tail=tail)

    if outcome.exit_code != 0:
        raise StageFailed(
            stage, outcome.exit_code, "the pinned binary exited non-zero", stderr_tail=tail
        )

    report = parse_stage_report(
        outcome.stdout,
        stage=stage,
        max_bytes=config.max_report_bytes,
        redactor=redactor,
    )
    try:
        metadata = expected_output.lstat()
    except OSError:
        metadata = None
    if (
        metadata is None
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > config.max_stage_artifact_bytes
    ):
        raise StageFailed(
            stage,
            outcome.exit_code,
            "the stage output is not a bounded non-empty regular file",
            stderr_tail=tail,
        )

    return StageReport(
        stage=stage,
        argv=outcome.argv,
        exit_code=outcome.exit_code,
        duration_ms=outcome.duration_ms,
        stdout_bytes=outcome.stdout_bytes,
        stderr_bytes=outcome.stderr_bytes,
        stderr_tail=tail,
        report=report,
    )


def _plan_rejection(
    outcome: ProcessOutcome,
    *,
    config: NativeToolchainConfig,
    redactor: SecretRedactor,
    tail: str,
) -> DataPlanRejected:
    """Turn a Data Tools exit 2 into a typed, diagnostics-carrying rejection.

    Invalid-plan rejections may omit row evidence. A parsable data-mapping
    envelope must carry a valid complete row-diagnostics document.
    """
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    for raw in (outcome.stdout, outcome.stderr):
        document = _safe_report(raw, config=config, redactor=redactor)
        if document is None:
            continue
        found = extract_diagnostics(document, stage=PipelineStage.DATA_PLAN)
        if _is_data_mapping(document) and not _has_row_diagnostics(document):
            raise MalformedStageOutput(
                PipelineStage.DATA_PLAN,
                "data_mapping rejection requires complete row_diagnostics",
            )
        if found:
            diagnostics = diagnostics + tuple(
                entry for entry in found if entry not in diagnostics
            )
    return DataPlanRejected(
        outcome.exit_code, diagnostics, stderr_tail=tail if not diagnostics else ""
    )


def _is_data_mapping(document: Mapping[str, Any]) -> bool:
    error = document.get("error")
    return isinstance(error, dict) and error.get("code") == "data_mapping"


def _has_row_diagnostics(document: Mapping[str, Any]) -> bool:
    if isinstance(document.get("row_diagnostics"), dict):
        return True
    error = document.get("error")
    return isinstance(error, dict) and isinstance(error.get("row_diagnostics"), dict)


def _safe_report(
    raw: bytes, *, config: NativeToolchainConfig, redactor: SecretRedactor
) -> Mapping[str, Any] | None:
    stripped = raw.strip()
    if not stripped:
        return None
    last_nonempty = next(
        (line.strip() for line in reversed(raw.splitlines()) if line.strip()), b""
    )
    if stripped[:1] not in {b"{", b"["} and last_nonempty[:1] not in {b"{", b"["}:
        # Plain-text stderr is retained only as a bounded redacted tail. Once
        # either the whole stream or its final report line looks like JSON,
        # strict parsing is mandatory and errors propagate.
        return None
    return parse_stage_report(
        raw,
        stage=PipelineStage.DATA_PLAN,
        max_bytes=config.max_report_bytes,
        redactor=redactor,
    )


def _validate_request(
    request: NativePipelineRequest, config: NativeToolchainConfig
) -> None:
    source = request.source
    if not _ENV_VAR_PATTERN.match(source.dsn_env_var):
        raise InvalidNativeRequest(
            f"{source.dsn_env_var!r} is not a valid environment variable name",
            details={"field": "dsn_env_var"},
        )
    if not source.dsn_env_var.startswith("PLENORA_"):
        raise InvalidNativeRequest(
            "dsn_env_var must use the reserved PLENORA_ application prefix",
            details={"field": "dsn_env_var"},
        )
    reserved_env_names = {*config.env_allowlist, "TMPDIR", "TEMP", "TMP"}
    if source.dsn_env_var in reserved_env_names:
        raise InvalidNativeRequest(
            f"{source.dsn_env_var!r} is reserved by the native runner",
            details={"field": "dsn_env_var"},
        )
    identifiers = [("schema", source.schema), ("table", source.table)]
    if source.order_by is not None:
        identifiers.append(("order_by", source.order_by))
    for field_name, value in identifiers:
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise InvalidNativeRequest(
                f"{field_name} is not a valid unquoted SQL identifier",
                details={"field": field_name},
            )
    if source.max_rows <= 0:
        raise InvalidNativeRequest(
            "max_rows must be a positive row count", details={"field": "max_rows"}
        )
    if source.max_output_bytes <= 0:
        raise InvalidNativeRequest(
            "max_output_bytes must be a positive byte bound",
            details={"field": "max_output_bytes"},
        )
    try:
        plan_metadata = request.plan_path.lstat()
    except OSError:
        plan_metadata = None
    if (
        plan_metadata is None
        or stat.S_ISLNK(plan_metadata.st_mode)
        or not stat.S_ISREG(plan_metadata.st_mode)
    ):
        raise InvalidNativeRequest(
            f"the Data plan {request.plan_path} is not a regular nofollow file",
            details={"field": "plan_path", "path": str(request.plan_path)},
        )
    if not request.output_path.parent.is_dir():
        raise InvalidNativeRequest(
            f"the output directory {request.output_path.parent} does not exist",
            details={
                "field": "output_path",
                "path": str(request.output_path.parent),
            },
        )
    for field_name, value in (
        ("stage_timeout_ms", config.stage_timeout_ms),
        ("identity_timeout_ms", config.identity_timeout_ms),
        ("max_stdout_bytes", config.max_stdout_bytes),
        ("max_stderr_bytes", config.max_stderr_bytes),
        ("max_report_bytes", config.max_report_bytes),
        ("max_binary_bytes", config.max_binary_bytes),
        ("max_stage_artifact_bytes", config.max_stage_artifact_bytes),
        ("max_plan_bytes", config.max_plan_bytes),
    ):
        if value <= 0:
            raise InvalidNativeRequest(
                f"{field_name} must be positive", details={"field": field_name}
            )
    if source.max_output_bytes > config.max_stage_artifact_bytes:
        raise InvalidNativeRequest(
            "max_output_bytes exceeds the configured stage artifact cap",
            details={
                "field": "max_output_bytes",
                "limit_bytes": config.max_stage_artifact_bytes,
            },
        )


def _snapshot_plan(
    source: Path, workspace: Workspace, config: NativeToolchainConfig
) -> Path:
    destination = workspace.stage_path("00-plan.json")
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise InvalidNativeRequest(
            f"cannot open Data plan without following links: {exc}",
            details={"field": "plan_path", "path": str(source)},
        ) from exc
    try:
        metadata = os.fstat(source_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > config.max_plan_bytes
        ):
            raise InvalidNativeRequest(
                "the Data plan is empty, non-regular, or exceeds max_plan_bytes",
                details={"field": "plan_path", "path": str(source)},
            )
        destination_fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            os.fchmod(destination_fd, 0o600)
            copied = 0
            while True:
                chunk = os.read(source_fd, min(1024 * 1024, config.max_plan_bytes + 1 - copied))
                if not chunk:
                    break
                copied += len(chunk)
                if copied > config.max_plan_bytes:
                    raise InvalidNativeRequest(
                        "the Data plan grew beyond max_plan_bytes",
                        details={"field": "plan_path", "path": str(source)},
                    )
                view = memoryview(chunk)
                while view:
                    view = view[os.write(destination_fd, view) :]
            if copied != metadata.st_size:
                raise InvalidNativeRequest(
                    "the Data plan changed while being snapshotted",
                    details={"field": "plan_path", "path": str(source)},
                )
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)
    return destination


def _read_dsn(env_var: str) -> str:
    value = settings.get_raw(env_var)
    if value is None or not str(value).strip():
        raise InvalidNativeRequest(
            f"the environment variable {env_var} carries no DSN",
            details={"field": "dsn_env_var", "env_var": env_var},
        )
    return str(value)


__all__ = ["run_native_pipeline"]
