"""Argv builders for the pinned CLI contracts.

These are the only places that know a subcommand name or a flag order. Each
builder returns a complete argv list; nothing here is ever joined into a
string, and no secret value is ever placed on a command line — the DSN travels
as an environment variable *name*.
"""

from __future__ import annotations

from pathlib import Path

from app.core.native_pipeline.config import NativeToolchainConfig
from app.core.native_pipeline.models import NativePipelineRequest


def database_read_argv(
    config: NativeToolchainConfig,
    request: NativePipelineRequest,
    output_path: Path,
) -> tuple[str, ...]:
    """`plenora-database postgres-read-ipc ENV_VAR schema table output ...`."""
    source = request.source
    argv = [
        *config.database.argv_prefix,
        "postgres-read-ipc",
        source.dsn_env_var,
        source.schema,
        source.table,
        str(output_path),
        "--max-rows",
        str(source.max_rows),
        "--max-output-bytes",
        str(source.max_output_bytes),
        "--timeout-ms",
        str(config.stage_timeout_ms),
    ]
    if source.order_by:
        argv.extend(["--order-by", source.order_by])
    return tuple(argv)


def data_plan_argv(
    config: NativeToolchainConfig,
    *,
    plan_path: Path,
    input_path: Path,
    output_path: Path,
) -> tuple[str, ...]:
    """`plenora-data-tools run --plan plan.json --input in --output out`."""
    return (
        *config.data.argv_prefix,
        "run",
        "--plan",
        str(plan_path),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    )


def io_convert_argv(
    config: NativeToolchainConfig, *, input_path: Path, output_path: Path
) -> tuple[str, ...]:
    """`plenora-io convert input.arrow output.arrow`."""
    return (*config.io.argv_prefix, "convert", str(input_path), str(output_path))


__all__ = ["data_plan_argv", "database_read_argv", "io_convert_argv"]
