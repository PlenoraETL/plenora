"""CLI renderer della coverage matrix.

Uso:
    python3 coverage/render.py --format markdown --output coverage/matrix.md
    python3 coverage/render.py --format html --output coverage/matrix.html
    python3 coverage/render.py --format json --output coverage/matrix.json

Se `--output` e' omesso scrive su stdout. Output deterministico:
nessun timestamp dinamico, ordinamento esplicito su righe/colonne/celle.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List

# Permetti l'esecuzione da qualunque cwd.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from matrices import ALL_MATRICES, Matrix  # noqa: E402
from coverage_parser import (  # noqa: E402
    CoverageData,
    CoverageStats,
    TestRef,
    build_coverage,
    cell_key,
    compute_stats,
    top_gaps,
)


# ---------------------------------------------------------------------------
# Cell rendering helpers
# ---------------------------------------------------------------------------
DONE = "DONE"


def _cell_state(tests: Iterable[TestRef]) -> str:
    """Ritorna 'done' | 'todo' | 'skip' | 'empty'."""
    tests = list(tests)
    if not tests:
        return "empty"
    statuses = {t.status for t in tests}
    if DONE in statuses:
        return "done"
    if statuses == {"SKIP"}:
        return "skip"
    return "todo"


def _cell_marker(tests: List[TestRef]) -> str:
    """Marker compatto per il rendering markdown."""
    state = _cell_state(tests)
    n = len(tests)
    if state == "empty":
        return "[ ] 0"
    if state == "done":
        return f"[x] {n}"
    if state == "skip":
        return f"[~] {n}"
    return f"[.] {n}"


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------
_MD_LEGEND = (
    "Legenda: `[x]` >=1 test DONE  |  `[.]` solo TODO/WIP  |  "
    "`[~]` solo SKIP  |  `[ ]` nessun test catalogato"
)


def render_markdown(data: CoverageData) -> str:
    stats = compute_stats(data)
    gaps = top_gaps(data, limit=5)

    out: List[str] = []
    out.append("# Plenora E2E - Coverage Matrix")
    out.append("")
    out.append(
        f"Sorgente catalogo: **{data.source}**  |  "
        f"Test totali: **{len(data.tests)}**"
    )
    out.append("")
    out.append(_MD_LEGEND)
    out.append("")

    # --- Executive summary ---
    out.append("## Executive summary")
    out.append("")
    out.append(f"- Celle totali: **{stats.total_cells}**")
    out.append(
        f"- Coperte (>=1 test DONE): **{stats.covered_done}** "
        f"({stats.coverage_pct:.1f}%)"
    )
    out.append(
        f"- Catalogate (>=1 test TODO/WIP): **{stats.covered_any}** "
        f"({stats.catalogued_pct:.1f}%)"
    )
    out.append(f"- Solo catalogate (no DONE): **{stats.catalogued_only}**")
    out.append(f"- Scoperte (0 test): **{stats.uncovered}**")
    out.append("")
    if gaps:
        out.append("**Top 5 gap (celle 0-test in matrici critiche):**")
        out.append("")
        for matrix_name, r, c in gaps:
            out.append(f"- `{matrix_name}` :: {r} x {c}")
        out.append("")

    # --- Matrici ---
    for matrix in ALL_MATRICES:
        out.extend(_render_matrix_md(matrix, data))
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def _render_matrix_md(matrix: Matrix, data: CoverageData) -> List[str]:
    out: List[str] = []
    out.append(f"## {matrix.title}")
    out.append("")
    out.append(f"_{matrix.description}_")
    out.append("")

    header = ["row \\ col", *matrix.cols]
    out.append("| " + " | ".join(header) + " |")
    out.append("|" + "|".join(["---"] * len(header)) + "|")

    cells_data = data.by_matrix[matrix.name]
    detail_rows: List[tuple[str, str, List[TestRef]]] = []

    for r in matrix.rows:
        row = [f"`{r}`"]
        for c in matrix.cols:
            tests = list(cells_data[cell_key(r, c)])
            row.append(_cell_marker(tests))
            if tests:
                detail_rows.append((r, c, tests))
        out.append("| " + " | ".join(row) + " |")

    # Detail block
    if detail_rows:
        out.append("")
        out.append(f"### Detail — {matrix.name}")
        out.append("")
        for r, c, tests in detail_rows:
            ids = ", ".join(f"`{t.id}` ({t.status})" for t in tests)
            out.append(f"- **{r} x {c}**: {ids}")
    return out


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------
_HTML_CSS = """
body { font-family: system-ui, sans-serif; max-width: 1200px; margin: 2em auto; padding: 0 1em; }
h1, h2, h3 { color: #222; }
table { border-collapse: collapse; margin: 1em 0; font-size: 13px; }
th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: center; }
th { background: #f0f0f0; }
td.row-head { background: #fafafa; font-weight: 600; text-align: left; }
td.done { background: #c8f0c8; }
td.todo { background: #fff0b3; }
td.skip { background: #e0e0e0; color: #666; }
td.empty { background: #f8c8c8; color: #555; }
.tooltip { cursor: help; text-decoration: underline dotted; }
.legend span { display: inline-block; padding: 2px 8px; margin-right: 8px; border: 1px solid #ccc; }
.summary { background: #f4f4f8; padding: 1em; border-left: 4px solid #557; }
ul.gaps li { font-family: monospace; }
""".strip()


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_html(data: CoverageData) -> str:
    stats = compute_stats(data)
    gaps = top_gaps(data, limit=5)

    out: List[str] = []
    out.append("<!DOCTYPE html>")
    out.append('<html lang="it"><head>')
    out.append('<meta charset="utf-8">')
    out.append("<title>Plenora E2E - Coverage Matrix</title>")
    out.append(f"<style>{_HTML_CSS}</style>")
    out.append("</head><body>")
    out.append("<h1>Plenora E2E - Coverage Matrix</h1>")

    out.append('<div class="legend">')
    out.append('<span class="done">DONE</span>')
    out.append('<span class="todo">TODO/WIP</span>')
    out.append('<span class="skip">SKIP</span>')
    out.append('<span class="empty">no test</span>')
    out.append("</div>")

    out.append('<div class="summary">')
    out.append(f"<p>Sorgente catalogo: <b>{_esc(data.source)}</b> "
               f"&middot; Test totali: <b>{len(data.tests)}</b></p>")
    out.append("<ul>")
    out.append(f"<li>Celle totali: <b>{stats.total_cells}</b></li>")
    out.append(f"<li>Coperte (>=1 DONE): <b>{stats.covered_done}</b> "
               f"({stats.coverage_pct:.1f}%)</li>")
    out.append(f"<li>Catalogate (>=1 TODO/WIP): <b>{stats.covered_any}</b> "
               f"({stats.catalogued_pct:.1f}%)</li>")
    out.append(f"<li>Scoperte: <b>{stats.uncovered}</b></li>")
    out.append("</ul>")
    if gaps:
        out.append("<p><b>Top 5 gap (matrici critiche):</b></p>")
        out.append('<ul class="gaps">')
        for matrix_name, r, c in gaps:
            out.append(f"<li>{_esc(matrix_name)} :: {_esc(r)} x {_esc(c)}</li>")
        out.append("</ul>")
    out.append("</div>")

    for matrix in ALL_MATRICES:
        out.extend(_render_matrix_html(matrix, data))

    out.append("</body></html>")
    return "\n".join(out) + "\n"


def _render_matrix_html(matrix: Matrix, data: CoverageData) -> List[str]:
    out: List[str] = []
    out.append(f"<h2>{_esc(matrix.title)}</h2>")
    out.append(f"<p><i>{_esc(matrix.description)}</i></p>")
    out.append("<table>")
    out.append("<thead><tr><th>row \\ col</th>")
    for c in matrix.cols:
        out.append(f"<th>{_esc(c)}</th>")
    out.append("</tr></thead><tbody>")
    cells_data = data.by_matrix[matrix.name]
    for r in matrix.rows:
        out.append(f'<tr><td class="row-head">{_esc(r)}</td>')
        for c in matrix.cols:
            tests = list(cells_data[cell_key(r, c)])
            state = _cell_state(tests)
            n = len(tests)
            tooltip = ", ".join(f"{t.id} ({t.status})" for t in tests) if tests else "no test"
            cell_text = str(n) if n else "0"
            out.append(
                f'<td class="{state}"><span class="tooltip" '
                f'title="{_esc(tooltip)}">{cell_text}</span></td>'
            )
        out.append("</tr>")
    out.append("</tbody></table>")
    return out


# ---------------------------------------------------------------------------
# JSON renderer (machine-readable)
# ---------------------------------------------------------------------------
def render_json(data: CoverageData) -> str:
    stats = compute_stats(data)
    gaps = top_gaps(data, limit=5)
    payload = {
        "source": data.source,
        "totals": {
            "tests": len(data.tests),
            **asdict(stats),
        },
        "top_gaps": [
            {"matrix": m, "row": r, "col": c} for m, r, c in gaps
        ],
        "matrices": [],
    }
    for matrix in ALL_MATRICES:
        m_entry = {
            "name": matrix.name,
            "title": matrix.title,
            "description": matrix.description,
            "rows": list(matrix.rows),
            "cols": list(matrix.cols),
            "critical": matrix.critical,
            "cells": [],
        }
        cells_data = data.by_matrix[matrix.name]
        for r in matrix.rows:
            for c in matrix.cols:
                tests = list(cells_data[cell_key(r, c)])
                m_entry["cells"].append({
                    "row": r,
                    "col": c,
                    "state": _cell_state(tests),
                    "count": len(tests),
                    "tests": [
                        {"id": t.id, "status": t.status, "title": t.title}
                        for t in tests
                    ],
                })
        payload["matrices"].append(m_entry)
    return json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render coverage matrix")
    parser.add_argument(
        "--format", "-f",
        choices=("markdown", "html", "json"),
        default="markdown",
        help="output format (default: markdown)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="output file (default: stdout)",
    )
    args = parser.parse_args(argv)

    data = build_coverage()
    if args.format == "markdown":
        out = render_markdown(data)
    elif args.format == "html":
        out = render_html(data)
    else:
        out = render_json(data)

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
