#!/usr/bin/env python3
"""Read-only project canvas validation check."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import NoReturn

from app.db.pg_connection import fetchall
from app.shared.execution.canvas_graph import CanvasGraph


NO_INVALID_CANVASES = 0
INVALID_CANVASES = 1
OPERATIONAL_FAILURE = 2


class CanvasValidationError(ValueError):
    """Recoverable canvas validation failure for one project row."""


@dataclass(frozen=True)
class ProjectCanvasRow:
    project_id: str
    name: str
    canvas: object


@dataclass(frozen=True)
class InvalidProjectCanvas:
    project_id: str
    name: str
    error: str


FetchRows = Callable[[], Sequence[Mapping[str, object]]]


def _row_text(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    return str(value) if value is not None else ""


def _project_canvas_row(row: Mapping[str, object]) -> ProjectCanvasRow:
    return ProjectCanvasRow(
        project_id=_row_text(row, "id"),
        name=_row_text(row, "name"),
        canvas=row.get("canvas"),
    )


def _decode_canvas(raw_canvas: object) -> Mapping[str, object]:
    if raw_canvas is None or raw_canvas == "":
        return {"nodes": [], "edges": []}
    if isinstance(raw_canvas, str):
        try:
            decoded = json.loads(raw_canvas)
        except json.JSONDecodeError as exc:
            raise CanvasValidationError(f"invalid JSON: {exc.msg}") from exc
        if decoded is None:
            return {"nodes": [], "edges": []}
        raw_canvas = decoded
    if not isinstance(raw_canvas, Mapping):
        raise CanvasValidationError(
            f"canvas root must be an object, got {type(raw_canvas).__name__}"
        )
    return raw_canvas


def _sequence_field(canvas: Mapping[str, object], field_name: str) -> Sequence[object]:
    value = canvas.get(field_name, [])
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise CanvasValidationError(
            f"canvas.{field_name} must be a list, got {type(value).__name__}"
        )
    return value


def _validate_canvas(raw_canvas: object) -> None:
    canvas = _decode_canvas(raw_canvas)
    nodes = _sequence_field(canvas, "nodes")
    edges = _sequence_field(canvas, "edges")
    try:
        graph = CanvasGraph.from_canvas(canvas)
    except (TypeError, ValueError) as exc:
        raise CanvasValidationError(str(exc)) from exc

    if len(graph.raw_nodes) != len(nodes):
        raise CanvasValidationError(
            "canvas.nodes contains an item that is not an object or has no id"
        )
    if len(graph.edges) != len(edges):
        raise CanvasValidationError(
            "canvas.edges contains an item that is not an object"
        )


def scan_project_canvases(rows: Sequence[Mapping[str, object]]) -> list[InvalidProjectCanvas]:
    invalid: list[InvalidProjectCanvas] = []
    for raw_row in rows:
        row = _project_canvas_row(raw_row)
        try:
            _validate_canvas(row.canvas)
        except CanvasValidationError as exc:
            invalid.append(
                InvalidProjectCanvas(
                    project_id=row.project_id,
                    name=row.name,
                    error=str(exc),
                )
            )
    return invalid


def _fetch_project_canvas_rows() -> Sequence[Mapping[str, object]]:
    return fetchall(
        """
        SELECT id, name, canvas
        FROM projects
        ORDER BY id
        """
    )


def _print_invalid(invalid: Sequence[InvalidProjectCanvas]) -> None:
    print(f"INVALID project canvases: {len(invalid)}", file=sys.stderr)
    for item in invalid:
        print(
            f"- project_id={item.project_id!r} name={item.name!r}: {item.error}",
            file=sys.stderr,
        )


def run(fetch_rows: FetchRows = _fetch_project_canvas_rows) -> int:
    try:
        rows = fetch_rows()
        invalid = scan_project_canvases(rows)
    except Exception as exc:
        print(f"ERROR: operational failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return OPERATIONAL_FAILURE

    if invalid:
        _print_invalid(invalid)
        return INVALID_CANVASES

    print(f"OK: scanned {len(rows)} project canvases; none invalid.")
    return NO_INVALID_CANVASES


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description=(
            "Read-only check for corrupted project canvases. "
            "Run from backend with DATABASE_URL configured."
        ),
    )


def main() -> int:
    _build_parser().parse_args()
    try:
        return run()
    except KeyboardInterrupt:
        return 130


def _exit() -> NoReturn:
    sys.exit(main())


if __name__ == "__main__":
    _exit()
