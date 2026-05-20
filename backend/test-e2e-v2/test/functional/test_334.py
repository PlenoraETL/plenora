"""test_334 - spatial SQL registry regression guard.

Focus bug vicinity:
- SQL Server must not use nonexistent spatial method names;
- MySQL must not call unavailable ST_Covers/ST_CoveredBy functions directly;
- Oracle ST_Crosses must keep the line/polygon mask validated by the fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

from _e2e import assert_true


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def test() -> None:
    registry_path = _repo_root() / "backend" / "app" / "db" / "sql_functions_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    spatial = registry["spatial"]

    required_functions = [
        "ST_Intersects",
        "ST_Contains",
        "ST_Within",
        "ST_DWithin",
        "ST_Equals",
        "ST_Disjoint",
        "ST_Crosses",
        "ST_Covers",
        "ST_CoveredBy",
        "ST_Touches",
        "ST_Overlaps",
    ]
    required_dialects = {"postgres", "mssql", "mysql", "oracle", "sqlite"}
    for function_name in required_functions:
        assert_true(function_name in spatial, f"{function_name} must be registered")
        dialects = set(spatial[function_name].get("dialects") or {})
        assert_true(required_dialects <= dialects, f"{function_name} must support all spatial test dialects")

    mssql_touches = spatial["ST_Touches"]["dialects"]["mssql"]
    assert_true(".STTouches" not in mssql_touches, "SQL Server geography has no STTouches method in our runtime")
    assert_true("STDistance" in mssql_touches, "SQL Server ST_Touches must use distance-based geography emulation")

    mssql_crosses = spatial["ST_Crosses"]["dialects"]["mssql"]
    assert_true(".STCrosses" not in mssql_crosses, "SQL Server geography has no STCrosses method in our runtime")
    assert_true("STIntersection" in mssql_crosses, "SQL Server ST_Crosses must use emulation")

    mysql_covers = spatial["ST_Covers"]["dialects"]["mysql"]
    mysql_covered_by = spatial["ST_CoveredBy"]["dialects"]["mysql"]
    assert_true("ST_Covers(" not in mysql_covers, "MySQL fixture runtime does not expose ST_Covers")
    assert_true("ST_CoveredBy(" not in mysql_covered_by, "MySQL fixture runtime does not expose ST_CoveredBy")
    assert_true("ST_Contains" in mysql_covers and "ST_Equals" in mysql_covers, "MySQL ST_Covers must be emulated")
    assert_true("ST_Within" in mysql_covered_by and "ST_Equals" in mysql_covered_by, "MySQL ST_CoveredBy must be emulated")

    oracle_crosses = spatial["ST_Crosses"]["dialects"]["oracle"]
    assert_true("OVERLAPBDYDISJOINT" in oracle_crosses, "Oracle line/polygon ST_Crosses must use OVERLAPBDYDISJOINT")


if __name__ == "__main__":
    test()
