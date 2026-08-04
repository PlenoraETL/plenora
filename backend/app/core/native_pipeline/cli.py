"""Machine-readable CLI for the native sidecar path.

    python -m app.core.native_pipeline run --dsn-env PG_DSN --schema public \
        --table misure --plan plan.json --output out.arrow

The DSN is named, never passed: ``--dsn-env`` takes the *name* of the
environment variable holding it. Every outcome is one JSON document on stdout —
``{"status": "ok", ...}`` with exit code 0, or ``{"status": "error", ...}``
carrying a typed code with a non-zero exit code.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from app.core.native_pipeline.capabilities import (
    PipelineOperation,
    SourceKind,
    capability_matrix,
)
from app.core.native_pipeline.config import NativeToolchainConfig
from app.core.native_pipeline.errors import NativePipelineError
from app.core.native_pipeline.models import NativePipelineRequest, TableReadSpec
from app.core.native_pipeline.pipeline import run_native_pipeline


_EXIT_OK = 0
_EXIT_ERROR = 1

_DEFAULT_MAX_ROWS = 1_000_000
_DEFAULT_MAX_OUTPUT_BYTES = 512 * 1024 * 1024


class _ArgumentError(ValueError):
    pass


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ArgumentError(message)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser of the native path CLI."""
    parser = _JsonArgumentParser(
        prog="python -m app.core.native_pipeline",
        description=(
            "Explicit, capability-gated native path over the pinned "
            "plenora-database / plenora-data-tools / plenora-IO-tools releases."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "capabilities", help="print the capability matrix of the pinned releases"
    )

    run_parser = subparsers.add_parser("run", help="run one read -> plan -> convert")
    run_parser.add_argument(
        "--dsn-env",
        required=True,
        help="name of the environment variable holding the DSN (never the value)",
    )
    run_parser.add_argument("--schema", required=True)
    run_parser.add_argument("--table", required=True)
    run_parser.add_argument("--plan", required=True, type=Path)
    run_parser.add_argument("--output", required=True, type=Path)
    run_parser.add_argument("--max-rows", type=int, default=_DEFAULT_MAX_ROWS)
    run_parser.add_argument(
        "--max-output-bytes", type=int, default=_DEFAULT_MAX_OUTPUT_BYTES
    )
    run_parser.add_argument("--order-by", default=None)
    run_parser.add_argument(
        "--source",
        default=SourceKind.POSTGRES.value,
        choices=[kind.value for kind in SourceKind],
    )
    run_parser.add_argument(
        "--operation",
        default=PipelineOperation.READ.value,
        choices=[operation.value for operation in PipelineOperation],
    )
    return parser


def _request_from_args(args: argparse.Namespace) -> NativePipelineRequest:
    return NativePipelineRequest(
        source=TableReadSpec(
            dsn_env_var=args.dsn_env,
            schema=args.schema,
            table=args.table,
            max_rows=args.max_rows,
            max_output_bytes=args.max_output_bytes,
            order_by=args.order_by,
        ),
        plan_path=args.plan,
        output_path=args.output,
        source_kind=SourceKind(args.source),
        operation=PipelineOperation(args.operation),
    )


def _capabilities_document() -> Mapping[str, Any]:
    return {
        "status": "ok",
        "capabilities": [
            {
                "capability": capability.value,
                "supported": support.supported,
                "reason": support.reason,
            }
            for capability, support in capability_matrix().items()
        ]
    }


def _error_document(
    code: str, message: str, details: Mapping[str, Any] | None = None
) -> Mapping[str, Any]:
    return {
        "status": "error",
        "error": {
            "code": code,
            "message": message,
            "details": dict(details or {}),
        },
    }


def _emit(document: Mapping[str, Any]) -> None:
    try:
        payload = json.dumps(
            document, ensure_ascii=True, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError):
        payload = json.dumps(
            _error_document(
                "serialization_error",
                "the CLI result could not be represented as strict JSON",
            ),
            ensure_ascii=True,
            sort_keys=True,
            allow_nan=False,
        )
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: returns the process exit code, never raises on run errors."""
    try:
        args = build_parser().parse_args(None if argv is None else list(argv))
    except _ArgumentError as exc:
        _emit(_error_document("invalid_arguments", str(exc)))
        return _EXIT_ERROR

    if args.command == "capabilities":
        _emit(_capabilities_document())
        return _EXIT_OK

    try:
        config = NativeToolchainConfig.from_settings()
        result = run_native_pipeline(_request_from_args(args), config)
    except NativePipelineError as exc:
        _emit(_error_document(exc.code, str(exc), exc.details))
        return _EXIT_ERROR
    except ValueError as exc:
        # A malformed pinning is a configuration defect, reported with the same
        # machine-readable envelope so operators do not have to parse a stack.
        _emit(_error_document("invalid_configuration", str(exc)))
        return _EXIT_ERROR
    except OSError as exc:
        # Filesystem and process-table failures are infrastructure, not
        # contract violations, but the CLI still answers with one JSON
        # document: a caller parsing stdout never has to handle a traceback.
        _emit(_error_document("io_error", str(exc)))
        return _EXIT_ERROR

    _emit({"status": "ok", "result": result.to_json_mapping()})
    return _EXIT_OK


__all__ = ["build_parser", "main"]
