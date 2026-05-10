"""Gate CI per la coverage matrix.

Uso:
    python3 coverage/check.py --min-coverage 60
    python3 coverage/check.py --min-coverage 60 --metric catalogued

Exit code:
    0  coverage >= soglia
    1  coverage < soglia
    2  errore di parsing/configurazione

Stampa una sintesi su stdout (anche in caso di success).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

# Permetti l'esecuzione da qualunque cwd.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from coverage_parser import build_coverage, compute_stats, top_gaps  # noqa: E402


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CI gate per coverage matrix")
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=60.0,
        help="soglia minima percentuale (default: 60.0)",
    )
    parser.add_argument(
        "--metric",
        choices=("done", "catalogued"),
        default="done",
        help="quale metrica usare per il gate "
             "(done = test DONE; catalogued = anche TODO/WIP). Default: done",
    )
    parser.add_argument(
        "--show-gaps",
        type=int,
        default=10,
        help="numero di gap (celle 0-test in matrici critiche) da stampare",
    )
    args = parser.parse_args(argv)

    try:
        data = build_coverage()
    except Exception as exc:  # pragma: no cover
        print(f"[coverage-check] errore caricamento catalogo: {exc}", file=sys.stderr)
        return 2

    stats = compute_stats(data)
    actual = stats.coverage_pct if args.metric == "done" else stats.catalogued_pct
    label = "DONE" if args.metric == "done" else "catalogued"

    print(f"[coverage-check] sorgente: {data.source}")
    print(f"[coverage-check] test totali: {len(data.tests)}")
    print(f"[coverage-check] celle totali: {stats.total_cells}")
    print(f"[coverage-check] coverage DONE: {stats.coverage_pct:.1f}%")
    print(f"[coverage-check] coverage catalogued: {stats.catalogued_pct:.1f}%")
    print(f"[coverage-check] metrica gate: {label} (soglia {args.min_coverage:.1f}%)")
    print(f"[coverage-check] valore attuale: {actual:.1f}%")

    if args.show_gaps > 0:
        gaps = top_gaps(data, limit=args.show_gaps)
        if gaps:
            print(f"[coverage-check] top {len(gaps)} gap (matrici critiche):")
            for matrix_name, r, c in gaps:
                print(f"  - {matrix_name} :: {r} x {c}")

    if actual + 1e-9 < args.min_coverage:
        print(
            f"[coverage-check] FAIL: {actual:.1f}% < {args.min_coverage:.1f}%",
            file=sys.stderr,
        )
        return 1
    print(f"[coverage-check] OK: {actual:.1f}% >= {args.min_coverage:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
