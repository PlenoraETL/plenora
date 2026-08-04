"""Bounded parsing of the JSON documents the pinned binaries emit.

The parser is deliberately narrow: a stage report is either absent (empty
stream) or a single JSON object. Two parse attempts are made — the whole
stream, then its last non-empty line, which is the shape a tool that logs
progress before its report produces. Anything else is a typed failure, never a
best-effort guess.
"""

from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

from app.core.native_pipeline.errors import MalformedStageOutput
from app.core.native_pipeline.models import LossReport
from app.core.native_pipeline.redaction import SecretRedactor
from app.core.native_pipeline.stages import PipelineStage


_STDERR_TAIL_CHARS = 4_000


def decode_stream(raw: bytes, redactor: SecretRedactor) -> str:
    """Decode child output to redacted text without raising on bad bytes."""
    return redactor.redact(raw.decode("utf-8", errors="replace"))


def stderr_tail(raw: bytes, redactor: SecretRedactor) -> str:
    """Return a bounded, redacted tail of a child's stderr."""
    return decode_stream(raw, redactor).strip()[-_STDERR_TAIL_CHARS:]


def parse_stage_report(
    raw: bytes,
    *,
    stage: PipelineStage,
    max_bytes: int,
    redactor: SecretRedactor,
) -> Mapping[str, Any] | None:
    """Parse a stage report, or return ``None`` for an empty stream."""
    if len(raw) > max_bytes:
        raise MalformedStageOutput(
            stage, f"report of {len(raw)} bytes exceeds the {max_bytes} byte bound"
        )
    text = decode_stream(raw, redactor).strip()
    if not text:
        return None

    document = _load_object(text)
    if document is None:
        raise MalformedStageOutput(
            stage, "expected a single JSON object on stdout"
        )
    return document


def _load_object(text: str) -> Mapping[str, Any] | None:
    for candidate in _candidates(text):
        try:
            parsed = strict_json_loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
        return None
    return None


def _candidates(text: str) -> tuple[str, ...]:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return (text,)
    return (text, lines[-1])


def extract_diagnostics(
    report: Mapping[str, Any] | None, *, stage: PipelineStage
) -> tuple[Mapping[str, Any], ...]:
    """Read the row-scoped diagnostics a stage declared."""
    if report is None:
        return ()

    entries: tuple[Mapping[str, Any], ...] = ()
    raw = report.get("diagnostics")
    if raw is not None:
        entries = _entries(raw, stage=stage, field_name="diagnostics")

    row_document = report.get("row_diagnostics")
    error = report.get("error")
    if row_document is None and isinstance(error, dict):
        row_document = error.get("row_diagnostics")
    if row_document is None:
        return entries
    if not isinstance(row_document, dict):
        raise MalformedStageOutput(stage, "row_diagnostics must be a JSON object")
    _validate_row_diagnostics(row_document, stage=stage)
    return (*entries, row_document)


def strict_json_loads(text: str) -> Any:
    """Load strict JSON, rejecting non-finite numbers and duplicate keys."""
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value} is not allowed")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for key, value in pairs:
            if key in document:
                raise ValueError(f"duplicate JSON key {key!r} is not allowed")
            document[key] = value
        return document

    def reject_nonfinite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite JSON number {value} is not allowed")
        return parsed

    return json.loads(
        text,
        parse_constant=reject_constant,
        parse_float=reject_nonfinite_float,
        object_pairs_hook=reject_duplicate_keys,
    )


def _validate_row_diagnostics(
    document: Mapping[str, Any], *, stage: PipelineStage
) -> None:
    required_literals = {
        "contract": "plenora-row-diagnostics-v1",
        "completeness": "complete",
        "index_basis": "source_row_zero_based",
    }
    for field, expected in required_literals.items():
        if document.get(field) != expected:
            raise MalformedStageOutput(stage, f"row_diagnostics.{field} is invalid")
    total = document.get("total")
    observed = document.get("observed_total")
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
        or not isinstance(observed, int)
        or isinstance(observed, bool)
        or observed < 0
        or total != observed
    ):
        raise MalformedStageOutput(
            stage, "row_diagnostics total and observed_total must be equal nonnegative integers"
        )
    counts = document.get("counts")
    if not isinstance(counts, dict):
        raise MalformedStageOutput(stage, "row_diagnostics.counts must be an object")
    count_total = 0
    for cause, count in counts.items():
        if (
            not isinstance(cause, str)
            or not cause
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise MalformedStageOutput(stage, "row_diagnostics.counts is invalid")
        count_total += count
    if count_total != total:
        raise MalformedStageOutput(stage, "row_diagnostics.counts does not sum to total")
    examples = document.get("examples")
    if not isinstance(examples, list):
        raise MalformedStageOutput(stage, "row_diagnostics.examples must be an array")
    for example in examples:
        if not isinstance(example, dict):
            raise MalformedStageOutput(stage, "row_diagnostics.examples entries must be objects")
        source_index, cause = example.get("source_index"), example.get("cause")
        if (
            not isinstance(source_index, int)
            or isinstance(source_index, bool)
            or source_index < 0
            or not isinstance(cause, str)
            or not cause.strip()
        ):
            raise MalformedStageOutput(stage, "row_diagnostics.examples entry is invalid")


def extract_loss(
    report: Mapping[str, Any] | None, *, stage: PipelineStage
) -> LossReport:
    """Read the loss manifest the IO stage declared."""
    if report is None:
        return LossReport(declared=False, entries=())
    raw = report.get("loss")
    if raw is None:
        return LossReport(declared=False, entries=())
    if isinstance(raw, list):
        entries = _entries(raw, stage=stage, field_name="loss")
        return LossReport(declared=bool(entries), entries=entries)
    if isinstance(raw, dict):
        entries = _entries(
            raw.get("entries", []), stage=stage, field_name="loss.entries"
        )
        declared = raw.get("declared", bool(entries))
        if not isinstance(declared, bool):
            raise MalformedStageOutput(stage, "loss.declared must be a boolean")
        return LossReport(declared=declared, entries=entries)
    raise MalformedStageOutput(
        stage, "loss must be a JSON object or a JSON array of entries"
    )


def _entries(
    raw: Any, *, stage: PipelineStage, field_name: str
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, (list, tuple)):
        raise MalformedStageOutput(stage, f"{field_name} must be a JSON array")
    entries: list[Mapping[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise MalformedStageOutput(
                stage, f"{field_name} entries must be JSON objects"
            )
        entries.append(entry)
    return tuple(entries)


def merge_diagnostics(
    groups: Sequence[Sequence[Mapping[str, Any]]],
) -> tuple[Mapping[str, Any], ...]:
    """Concatenate per-stage diagnostics in stage order."""
    merged: list[Mapping[str, Any]] = []
    for group in groups:
        merged.extend(group)
    return tuple(merged)


__all__ = [
    "decode_stream",
    "extract_diagnostics",
    "extract_loss",
    "merge_diagnostics",
    "parse_stage_report",
    "stderr_tail",
    "strict_json_loads",
]
