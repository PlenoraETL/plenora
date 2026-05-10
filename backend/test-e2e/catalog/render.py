#!/usr/bin/env python3
"""Render the structured catalog (test-e2e/catalog/*.yaml) into the legacy
markdown shape (test-e2e/test-e2e.md).

The YAML files are the single source of truth. This script regenerates the
markdown deterministically (sorted by area, then by test id), prepending an
auto-stats block.

Usage:

    python3 test-e2e/catalog/render.py

By default the markdown is written next to the catalog (test-e2e/test-e2e.md).
A different output can be chosen with `--output`.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# We avoid PyYAML at runtime: PyYAML 6.0.3 + Python 3.14 has a sporadic
# interpreter bug that corrupts local variables in `yaml/scanner.py` and
# triggers TypeError or segfault non-deterministically. Our restricted
# catalog dialect is parsed by a small in-tree module instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _yaml_lite import safe_load as _safe_load_text  # noqa: E402


def _safe_load(stream) -> object:
    text = stream.read() if hasattr(stream, "read") else stream
    # Python 3.14 has sporadic local-variable corruption (rare TypeError on
    # things like `str.startswith` mis-binding). Retry on TypeError.
    last_err: Exception | None = None
    for _ in range(10):
        try:
            return _safe_load_text(text)
        except TypeError as exc:
            last_err = exc
            continue
    assert last_err is not None
    raise last_err


CATALOG_DIR = Path(__file__).resolve().parent
TEST_E2E_DIR = CATALOG_DIR.parent
DEFAULT_OUTPUT = TEST_E2E_DIR / "test-e2e.md"

STATUSES = ["TODO", "WIP", "DONE", "SKIP", "META"]
# Canonical priority values (in display order):
#   - must:     smoke critici / gate merge / regression core
#   - nice:     CI nightly / valore alto ma non bloccante
#   - optional: edge case secondari, scriverli solo se i sopra sono fatti
#   - meta:     policy / cross-cutting / non test eseguibile autonomo
# `nice-to-have` (legacy) viene normalizzato a `nice` in output.
PRIORITIES = ["must", "nice", "optional", "meta"]


def _normalize_priority(value: str | None) -> str:
    if not value:
        return "nice"
    if value == "nice-to-have":
        return "nice"
    return value


def load_catalog() -> list[dict[str, Any]]:
    """Load every catalog/<area>.yaml (excluding _schema.yaml).

    Returns a list of {"area": ..., "description": ..., "tests": [...]} dicts.
    """
    files = sorted(p for p in CATALOG_DIR.glob("*.yaml") if not p.name.startswith("_"))
    result: list[dict[str, Any]] = []
    for fp in files:
        with fp.open(encoding="utf-8") as f:
            data = _safe_load(f)
        if not data or "area" not in data or "tests" not in data:
            print(f"WARN: {fp.name} missing area/tests, skipping", file=sys.stderr)
            continue
        # Sort tests deterministically by id (zero-padded numeric tail = lex sort).
        data["tests"] = sorted(data["tests"], key=lambda t: t["id"])
        result.append(data)
    # Sort areas by canonical order (legacy ordering preserved by sorted file names
    # since each area lives in its own file). Use first test id as the area key
    # so that area "prep" (PREP-*) ends up before "api" (API-*) in alphabetical
    # mode... but the canonical legacy ordering used a custom narrative flow.
    # We use the AREA_ORDER list below to mimic it; areas not listed go after.
    order_map = {area: i for i, area in enumerate(AREA_ORDER)}
    result.sort(key=lambda d: (order_map.get(d["area"], 999), d["area"]))
    return result


# Canonical legacy section order. Mirrors test-e2e_LEGACY.md.
AREA_ORDER = [
    "prep", "api", "ext", "man", "enr", "load", "arc", "sch", "auth",
    "inf", "sec", "ssrf", "sp", "sftp", "cloud", "qstr", "stg", "mfa",
    "pwd", "audit", "adm", "conc", "res", "int", "bck", "tenant", "perm",
    "mig", "perf", "size", "loc", "undo", "flow", "rate", "hook", "cors",
    "log", "idem", "beat", "catch", "recon", "ret", "clean", "intm",
    "shr", "sys", "task", "tkt", "files", "imdf", "reqid", "eseg",
]


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def fmt_steps(value: str) -> str:
    """Render a multi-line steps block (already a string)."""
    text = value.strip()
    return text


def fmt_assertion(value: str) -> str:
    return value.strip()


def render_test(t: dict[str, Any]) -> list[str]:
    """Render a single test as legacy-style markdown lines."""
    lines: list[str] = []
    lines.append(f"### {t['id']} — {t['title']}")
    lines.append(f"- **Stato**: {t.get('status', 'TODO')}")

    prereq = t.get("prereq") or {}
    profile = prereq.get("profile")
    prereq_tests = prereq.get("tests") or []
    parts: list[str] = []
    if prereq_tests:
        parts.extend(prereq_tests)
    if profile:
        parts.append(f"profilo `{profile}`")
    if parts:
        lines.append(f"- **Prereq**: {' + '.join(parts)}")

    refs = t.get("references") or []
    if refs:
        lines.append("- **Riferimento**: " + ", ".join(f"`{r}`" for r in refs))

    if t.get("dataset"):
        lines.append(f"- **Dataset**: {t['dataset']}")

    if t.get("steps"):
        steps_text = fmt_steps(t["steps"])
        if "\n" in steps_text:
            # multi-line: render with 2-space indent for line continuation
            first, *rest = steps_text.splitlines()
            lines.append(f"- **Passi**: {first}")
            for r in rest:
                lines.append(f"  {r}")
        else:
            lines.append(f"- **Passi**: {steps_text}")

    if t.get("assertion"):
        ass = fmt_assertion(t["assertion"])
        if "\n" in ass:
            first, *rest = ass.splitlines()
            lines.append(f"- **Asserzione**: {first}")
            for r in rest:
                lines.append(f"  {r}")
        else:
            lines.append(f"- **Asserzione**: {ass}")

    # Tags / granularity / priority surfaced as a compact meta line
    meta_parts = []
    gran = t.get("granularity")
    pri = _normalize_priority(t.get("priority"))
    tags = t.get("tags") or []
    if gran:
        meta_parts.append(f"granularity={gran}")
    if pri:
        meta_parts.append(f"priority={pri}")
    if tags:
        meta_parts.append("tags=" + ",".join(tags))
    if meta_parts:
        lines.append(f"- **Meta**: {' · '.join(meta_parts)}")

    return lines


def section_title(area: str, description: str) -> str:
    return f"## {area.upper()} — {description}"


def render_priority_table(catalog: list[dict[str, Any]]) -> list[str]:
    """Render a compact priority distribution table.

    This is intended to live at the very top of the generated markdown
    (before the long stats block) as a quick-glance "where is the budget".
    """
    all_tests = [t for area in catalog for t in area["tests"]]
    total = len(all_tests)
    by_priority = Counter(_normalize_priority(t.get("priority")) for t in all_tests)

    lines: list[str] = ["## Distribuzione priorità", ""]
    lines.append("| priority | count |     % |")
    lines.append("|----------|------:|------:|")
    for pr in PRIORITIES:
        n = by_priority.get(pr, 0)
        pct = (n / total * 100) if total else 0
        lines.append(f"| {pr:8s} | {n:>5d} | {pct:>4.1f}% |")
    lines.append(f"| total    | {total:>5d} | 100.0% |")
    lines.append("")
    lines.append(
        "> Definizioni: **must** = gate merge (smoke/regression core); "
        "**nice** = CI nightly (valore alto, non bloccante); "
        "**optional** = edge case secondari (scrivere solo se i sopra sono fatti); "
        "**meta** = policy / cross-cutting (non test eseguibile autonomo)."
    )
    lines.append("")
    return lines


def render_stats(catalog: list[dict[str, Any]]) -> list[str]:
    all_tests = [t for area in catalog for t in area["tests"]]
    total = len(all_tests)

    by_status = Counter(t.get("status", "TODO") for t in all_tests)
    by_priority = Counter(_normalize_priority(t.get("priority")) for t in all_tests)
    by_area = OrderedDict((a["area"], len(a["tests"])) for a in catalog)

    lines: list[str] = ["## Statistiche (auto)", ""]
    lines.append(f"- **Totale test**: {total}")
    lines.append("")

    lines.append("### Per stato")
    for st in STATUSES:
        n = by_status.get(st, 0)
        pct = (n / total * 100) if total else 0
        lines.append(f"- `{st}`: {n} ({pct:.1f}%)")
    lines.append("")

    lines.append("### Per priorità")
    for pr in PRIORITIES:
        n = by_priority.get(pr, 0)
        pct = (n / total * 100) if total else 0
        lines.append(f"- `{pr}`: {n} ({pct:.1f}%)")
    lines.append("")

    lines.append("### Per area")
    lines.append("")
    lines.append("| Area | Count |")
    lines.append("|------|------:|")
    for area, count in by_area.items():
        lines.append(f"| `{area}` | {count} |")
    lines.append("")

    return lines


def render_markdown(catalog: list[dict[str, Any]]) -> str:
    out: list[str] = []
    out.append("# Plenora — Test E2E")
    out.append("")
    out.append("> Catalogo dei test end-to-end. **Generato automaticamente**")
    out.append("> da `test-e2e/catalog/*.yaml` via `test-e2e/catalog/render.py`.")
    out.append("> NON modificare a mano: editare i YAML e rieseguire il render.")
    out.append("")
    out.append("Ogni voce è uno scenario eseguibile sullo stack `test-e2e/docker/`.")
    out.append("La granularità è 'un canvas plenora → un'asserzione'.")
    out.append("")
    out.append("## Convenzioni")
    out.append("")
    out.append("- **ID test**: `<area>-<NNN>` (es. `EXT-001`, `LOAD-014`).")
    out.append("- **Stato**: `TODO` (da scrivere) · `WIP` (in stesura) · `DONE` (passa) · `SKIP` (rinviato).")
    out.append("- **Prereq**: profilo docker compose (`core`, `db`, `cloud`, `files`, `db-heavy`, `full`)")
    out.append("  e/o test che devono essere DONE prima.")
    out.append("- **Asserzione**: cosa il test verifica (output finale, error code, stato DB).")
    out.append("- **Meta**: granularity (smoke/integration/scenario), priority (must/nice/optional/meta), tags.")
    out.append("")

    out.extend(render_priority_table(catalog))
    out.extend(render_stats(catalog))
    out.append("---")
    out.append("")

    for area_block in catalog:
        area = area_block["area"]
        desc = area_block.get("description") or area
        out.append(section_title(area, desc))
        out.append("")
        for t in area_block["tests"]:
            out.extend(render_test(t))
            out.append("")
        out.append("---")
        out.append("")

    out.append("")
    out.append(
        f"<!-- generated at {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} "
        "by test-e2e/catalog/render.py -->"
    )
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render YAML catalog → markdown.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output markdown path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Don't write; just verify YAML loads and report counts.",
    )
    args = parser.parse_args(argv)

    catalog = load_catalog()
    total = sum(len(a["tests"]) for a in catalog)
    print(f"Loaded {len(catalog)} areas, {total} tests.")

    if args.check:
        return 0

    md = render_markdown(catalog)
    args.output.write_text(md, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
