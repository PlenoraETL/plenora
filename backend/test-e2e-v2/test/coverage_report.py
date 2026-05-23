"""Validate and summarize the E2E v2 functional coverage manifest.

This tool is intentionally static: it checks that declared coverage evidence
points to existing numbered E2E host tests or scenario modules. It does not
start Docker, call the API, or execute the E2E suite.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TEST_ROOT = Path(__file__).resolve().parent
FUNCTIONAL_ROOT = TEST_ROOT / "functional"
DEFAULT_MANIFEST = TEST_ROOT / "coverage_manifest.json"
VALID_STATUSES = {"covered", "partial", "planned", "not_applicable"}
TEST_REF_RE = re.compile(r"^test_(\d{3})(?:-test_(\d{3}))?$")


@dataclass
class CoverageItem:
    category_id: str
    item_id: str
    title: str
    status: str
    required: bool
    tests: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.category_id}.{self.item_id}"

    @property
    def has_evidence(self) -> bool:
        return bool(self.tests or self.modules)


@dataclass
class CoverageReport:
    items: list[CoverageItem]
    errors: list[str]
    gaps: list[str]

    @property
    def required_items(self) -> list[CoverageItem]:
        return [item for item in self.items if item.required]

    @property
    def covered_required_items(self) -> list[CoverageItem]:
        return [
            item
            for item in self.required_items
            if item.status in {"covered", "partial"} and item.has_evidence
        ]


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"manifest not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON manifest {path}: {exc}") from exc


def _expand_test_ref(ref: str) -> list[str]:
    match = TEST_REF_RE.match(ref)
    if not match:
        raise ValueError(f"invalid test reference {ref!r}; expected test_NNN or test_NNN-test_NNN")

    start = int(match.group(1))
    end = int(match.group(2) or start)
    if end < start:
        raise ValueError(f"invalid descending test range {ref!r}")
    return [f"test_{idx:03d}" for idx in range(start, end + 1)]


def _module_path(module_ref: str) -> Path:
    candidate = Path(module_ref)
    if candidate.is_absolute():
        return candidate
    return (FUNCTIONAL_ROOT / candidate).resolve()


def _validate_evidence(item: CoverageItem) -> list[str]:
    errors: list[str] = []

    for ref in item.tests:
        try:
            test_ids = _expand_test_ref(ref)
        except ValueError as exc:
            errors.append(f"{item.key}: {exc}")
            continue
        for test_id in test_ids:
            path = FUNCTIONAL_ROOT / f"{test_id}.py"
            if not path.exists():
                errors.append(f"{item.key}: missing evidence test {test_id} ({path})")

    for module_ref in item.modules:
        path = _module_path(module_ref)
        if not path.exists():
            errors.append(f"{item.key}: missing evidence module {module_ref!r} ({path})")

    return errors


def validate_manifest(path: Path = DEFAULT_MANIFEST) -> CoverageReport:
    manifest = _load_manifest(path)
    errors: list[str] = []
    gaps: list[str] = []
    items: list[CoverageItem] = []

    if manifest.get("version") != 1:
        errors.append("manifest.version must be 1")

    categories = manifest.get("categories")
    if not isinstance(categories, list) or not categories:
        errors.append("manifest.categories must be a non-empty list")
        return CoverageReport(items=items, errors=errors, gaps=gaps)

    seen_keys: set[str] = set()
    for category in categories:
        category_id = str(category.get("id") or "").strip()
        if not category_id:
            errors.append("category without id")
            continue
        raw_items = category.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            errors.append(f"{category_id}: category.items must be a non-empty list")
            continue

        for raw in raw_items:
            item_id = str(raw.get("id") or "").strip()
            status = str(raw.get("status") or "").strip()
            evidence = raw.get("evidence") or {}
            item = CoverageItem(
                category_id=category_id,
                item_id=item_id,
                title=str(raw.get("title") or item_id),
                status=status,
                required=bool(raw.get("required", False)),
                tests=list(evidence.get("tests") or []),
                modules=list(evidence.get("modules") or []),
            )
            items.append(item)

            if not item_id:
                errors.append(f"{category_id}: item without id")
                continue
            if item.key in seen_keys:
                errors.append(f"{item.key}: duplicate item key")
            seen_keys.add(item.key)

            if status not in VALID_STATUSES:
                errors.append(f"{item.key}: invalid status {status!r}")
            if item.required and status in {"planned", "not_applicable"}:
                gaps.append(f"{item.key}: required item is {status}")
            if item.required and status in {"covered", "partial"} and not item.has_evidence:
                gaps.append(f"{item.key}: required item has no evidence")

            errors.extend(_validate_evidence(item))

    return CoverageReport(items=items, errors=errors, gaps=gaps)


def _print_report(report: CoverageReport) -> None:
    by_category: dict[str, list[CoverageItem]] = {}
    for item in report.items:
        by_category.setdefault(item.category_id, []).append(item)

    required_total = len(report.required_items)
    required_covered = len(report.covered_required_items)
    print(f"E2E v2 coverage manifest: {required_covered}/{required_total} required items have evidence")
    print(f"Total capability items: {len(report.items)}")

    for category_id, items in by_category.items():
        covered = sum(1 for item in items if item.status == "covered")
        partial = sum(1 for item in items if item.status == "partial")
        planned = sum(1 for item in items if item.status == "planned")
        print(f"- {category_id}: {covered} covered, {partial} partial, {planned} planned")

    if report.gaps:
        print("\nGaps:")
        for gap in report.gaps:
            print(f"- {gap}")

    if report.errors:
        print("\nManifest errors:")
        for error in report.errors:
            print(f"- {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true", help="emit machine-readable summary")
    parser.add_argument("--fail-on-gaps", action="store_true", help="return non-zero when required gaps exist")
    args = parser.parse_args(argv)

    try:
        report = validate_manifest(args.manifest)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "items": len(report.items),
                    "required": len(report.required_items),
                    "required_with_evidence": len(report.covered_required_items),
                    "errors": report.errors,
                    "gaps": report.gaps,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _print_report(report)

    if report.errors:
        return 2
    if args.fail_on_gaps and report.gaps:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
