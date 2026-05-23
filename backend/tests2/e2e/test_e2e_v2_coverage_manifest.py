"""Contract tests for the E2E v2 coverage manifest."""

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
E2E_TEST_ROOT = REPO_ROOT / "backend" / "test-e2e-v2" / "test"
sys.path.insert(0, str(E2E_TEST_ROOT))

from coverage_report import validate_manifest  # noqa: E402


def test_e2e_v2_coverage_manifest_has_valid_evidence() -> None:
    report = validate_manifest(E2E_TEST_ROOT / "coverage_manifest.json")

    assert report.errors == []
    assert report.gaps == []
    assert len(report.required_items) >= 10
    assert len(report.covered_required_items) == len(report.required_items)
