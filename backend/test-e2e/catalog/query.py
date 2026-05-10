#!/usr/bin/env python3
"""Query the structured test catalog.

Examples:

    python3 test-e2e/catalog/query.py --status TODO --priority must
    python3 test-e2e/catalog/query.py --area prep --json
    python3 test-e2e/catalog/query.py --tag postgres --status DONE
    python3 test-e2e/catalog/query.py --granularity scenario

Without filters: lists every test ID in the catalog.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# See render.py for why we avoid PyYAML at runtime.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _yaml_lite import safe_load as _safe_load_text  # noqa: E402


def _safe_load(stream) -> object:
    text = stream.read() if hasattr(stream, "read") else stream
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

VALID_STATUSES = {"TODO", "WIP", "DONE", "SKIP", "META"}
VALID_PRIORITIES = {"must", "nice", "optional", "meta"}
VALID_GRANULARITY = {"smoke", "integration", "scenario"}


def load_all_tests() -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    for fp in sorted(CATALOG_DIR.glob("*.yaml")):
        if fp.name.startswith("_"):
            continue
        with fp.open(encoding="utf-8") as f:
            data = _safe_load(f)
        if not data or "tests" not in data:
            continue
        area = data.get("area", fp.stem)
        for t in data["tests"]:
            t = dict(t)
            t.setdefault("area", area)
            tests.append(t)
    tests.sort(key=lambda t: t["id"])
    return tests


def matches(test: dict[str, Any], filters: argparse.Namespace) -> bool:
    if filters.status and test.get("status") != filters.status:
        return False
    if filters.area and test.get("area") != filters.area:
        return False
    if filters.priority and test.get("priority") != filters.priority:
        return False
    if filters.granularity and test.get("granularity") != filters.granularity:
        return False
    if filters.tag:
        tags = test.get("tags") or []
        if filters.tag not in tags:
            return False
    if filters.id_prefix:
        if not test.get("id", "").startswith(filters.id_prefix):
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Filter the YAML test catalog and print matching tests.",
    )
    parser.add_argument("--status", choices=sorted(VALID_STATUSES))
    parser.add_argument("--area", help="Area slug (e.g. prep, ext, intm)")
    parser.add_argument("--priority", choices=sorted(VALID_PRIORITIES))
    parser.add_argument("--granularity", choices=sorted(VALID_GRANULARITY))
    parser.add_argument("--tag", help="Tag to match (single)")
    parser.add_argument(
        "--id-prefix",
        dest="id_prefix",
        help="Match only tests whose id starts with this prefix (e.g. 'EXT-' or 'PREP-0').",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit full test objects as a JSON array instead of just IDs.",
    )
    parser.add_argument(
        "--count-only",
        action="store_true",
        help="Print only the number of matching tests.",
    )
    args = parser.parse_args(argv)

    tests = load_all_tests()
    matching = [t for t in tests if matches(t, args)]

    if args.count_only:
        print(len(matching))
        return 0

    if args.json:
        print(json.dumps(matching, indent=2, ensure_ascii=False))
        return 0

    for t in matching:
        print(t["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
