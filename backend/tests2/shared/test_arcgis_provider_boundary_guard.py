"""Architecture guard - ADR-0023: IMDF publish domain must not import
from the Features connection-management domain (app.core.features.*).

ArcGIS is an external read/write provider (macro block B - Connections).
IMDF is a publish consumer (macro block E - Resource/Storage).

Current state: two IMDF files still import Features internals (deferred).
These are documented as KNOWN_VIOLATIONS below - an explicit TODO list.
The guard fails on any ADDITIONAL cross-domain import, preventing the
boundary from widening further while the known violations are cleaned up.

Recommended fixes (see macro-components.md sec. 2.3):
  - Move EsriTypeMapper to app/shared/ or a provider-agnostic types module.
  - Replace FeaturesService import with a shared ArcGISClientProtocol or
    pass the connection info explicitly instead of looking it up via Features.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared]

_IMDF_DIR = Path(__file__).parents[2] / "app" / "core" / "imdf"

_FORBIDDEN_PREFIXES = ("app.core.features",)

# -----------------------------------------------------------------
# Known violations - TODO: fix these per macro-components.md section 2.3.
# Each entry is (filename -> exact imported modules). The guard is strict
# on all other files and on any new module inside known-bad files.
# Remove an entry only when the cross-domain import is gone.
# -----------------------------------------------------------------
_KNOWN_VIOLATIONS: dict[str, set[str]] = {
    "validator.py": {
        # create_client removed (batch 057-W6): validator now uses fs_svc._client.
        "app.core.features.features_service",
    },
    "arcgis_uploader.py": {
        "app.core.features.arcgis_client",
        "app.core.features.features_service",
    },
}

_KNOWN_VIOLATION_REASONS: dict[str, str] = {
    "validator.py": (
        "TODO ADR-0023: EsriTypeMapper moved to app/shared/esri_types (batch 056). "
        "create_client cut (batch 057-W6): validator reuses fs_svc._client. "
        "Remaining: FeaturesService connection lookup — replace with a shared "
        "ArcGIS connection leaf that does not import the Features domain."
    ),
    "arcgis_uploader.py": (
        "TODO ADR-0023: replace FeaturesService lookup with explicit ArcGIS "
        "connection info plus client protocol."
    ),
}


def _imdf_python_files() -> list[Path]:
    return sorted(_IMDF_DIR.rglob("*.py"))


def _imports_in_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
    return modules


@pytest.mark.parametrize("src_file", _imdf_python_files(), ids=lambda p: p.name)
def test_imdf_does_not_import_from_features_domain(src_file: Path) -> None:
    """IMDF modules must not import from app.core.features.* (ADR-0023).

    Known files may keep only the exact modules listed in
    _KNOWN_VIOLATIONS while the migration is pending. Any new Features
    import, including inside a known-bad file, fails.
    """
    allowed_known = _KNOWN_VIOLATIONS.get(src_file.name, set())
    seen_known: set[str] = set()

    for module in _imports_in_file(src_file):
        for prefix in _FORBIDDEN_PREFIXES:
            if module in allowed_known:
                seen_known.add(module)
                continue
            assert not module.startswith(prefix), (
                f"{src_file.name}: NEW cross-domain import detected "
                f"({module!r} starts with {prefix!r}). "
                "IMDF must not grow its dependency on the Features domain. "
                "See ADR-0023 and docs/architecture/macro-components.md sec. 2. "
                "If this is intentional, add to _KNOWN_VIOLATIONS with a reason."
            )

    if allowed_known and seen_known:
        pytest.skip(
            f"Known ADR-0023 violation remains in {src_file.name}: "
            f"{sorted(seen_known)}. {_KNOWN_VIOLATION_REASONS[src_file.name]}"
        )
