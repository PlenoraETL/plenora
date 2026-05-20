"""test_306 - MANIPOLA runtime registry coverage guard.

Scenario covered:
- the original registry matrix plus the new runtime tests cover every
  transform id currently present in transforms_registry.json.
"""

from __future__ import annotations

from _e2e import assert_equal
from _manipola_runtime_full import NEW_RUNTIME_TRANSFORMS, registry_transform_ids, run_silent, test_006_runtime_ids


def test_registry_runtime_coverage_is_complete() -> None:
    registry_ids = registry_transform_ids()
    covered_ids = test_006_runtime_ids() | NEW_RUNTIME_TRANSFORMS
    assert_equal(sorted(registry_ids - covered_ids), [], "all registry transforms must have direct runtime coverage")
    assert_equal(sorted(covered_ids - registry_ids), [], "runtime coverage list must not contain stale transform ids")


if __name__ == "__main__":
    run_silent(test_registry_runtime_coverage_is_complete)
