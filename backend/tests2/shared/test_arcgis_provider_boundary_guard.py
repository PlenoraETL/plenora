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

# Known violations - TODO: fix these per macro-components.md section 2.3.
# Each entry is (filename -> exact imported modules). The guard is strict
# on all other files and on any new module inside known-bad files.
# Remove an entry only when the cross-domain import is gone.
_KNOWN_VIOLATIONS: dict[str, set[str]] = {
    "validator.py": {
        # batch 059-W4: FeaturesService replaced by the thin adapter module.
        # The adapter is the one remaining cross-domain import; it encapsulates
        # the DB lookup so the validator does not duplicate that logic.
        "app.core.features.arcgis_connection_adapter",
    },
    "arcgis_uploader.py": {
        # batch 060-W2: FeaturesService + create_client replaced by the thin adapter.
        # Remaining: one cross-domain import — the same adapter as validator.py.
        "app.core.features.arcgis_connection_adapter",
    },
}

_KNOWN_VIOLATION_REASONS: dict[str, str] = {
    "validator.py": (
        "TODO ADR-0023: EsriTypeMapper moved to app/shared/esri_types (batch 056). "
        "FeaturesService replaced by arcgis_connection_adapter (batch 059-W4). "
        "Remaining: the adapter module is in app.core.features because the DB "
        "connection lookup (get_connection_db) lives there. Next cut: move the "
        "connection DB table to a shared/connection_repository leaf so the adapter "
        "can move to app.shared.connections without duplicating DB logic."
    ),
    "arcgis_uploader.py": (
        "TODO ADR-0023: FeaturesService + create_client replaced by "
        "arcgis_connection_adapter.resolve_arcgis_connection (batch 060-W2). "
        "Remaining: adapter lives in app.core.features because get_connection_db "
        "needs the DB. Next cut: connection repository leaf so adapter can move "
        "to app.shared.connections."
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


# Protocol shape contracts — BE-GO-READY-ARCH-058-W5


def test_arcgis_provider_protocol_importable_from_shared():
    """ArcGISProviderProtocol must be importable from the shared contracts
    package (not from app.core.features).  Moving it to shared is the first
    step toward removing the concrete client import from validator.py."""
    from app.shared.connections.arcgis_provider import ArcGISProviderProtocol
    assert callable(ArcGISProviderProtocol)


def test_arcgis_provider_protocol_has_required_methods():
    """The protocol must expose the three methods validator.py calls on client."""
    from app.shared.connections.arcgis_provider import ArcGISProviderProtocol
    for method in ("count_features_where", "list_layers", "get_layer_schema"):
        assert hasattr(ArcGISProviderProtocol, method), (
            f"ArcGISProviderProtocol missing method {method!r}. "
            "validator.py calls this method on the ArcGIS client."
        )


def test_arcgis_connection_info_importable_from_shared():
    """ArcGISConnectionInfo must be importable from app.shared.connections
    (not only from app.core.features.arcgis_client)."""
    from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
    assert callable(ArcGISConnectionInfo)


def test_arcgis_connection_info_backward_compat_via_arcgis_client():
    """arcgis_client.py must re-export ArcGISConnectionInfo so existing
    callers (features_service, test fixtures) are unaffected."""
    from app.core.features.arcgis_client import ArcGISConnectionInfo as FromClient
    from app.shared.connections.arcgis_connection import ArcGISConnectionInfo as FromShared
    assert FromClient is FromShared, (
        "arcgis_client.ArcGISConnectionInfo must be the same object as "
        "app.shared.connections.arcgis_connection.ArcGISConnectionInfo. "
        "The re-export in arcgis_client.py is missing or points to a copy."
    )


def test_arcgis_client_satisfies_provider_protocol():
    """ArcGISClient must satisfy ArcGISProviderProtocol structurally
    (runtime_checkable isinstance check).  This verifies the protocol
    accurately describes the concrete client's interface."""
    from app.core.features.arcgis_client import ArcGISClient
    from app.shared.connections.arcgis_provider import ArcGISProviderProtocol
    assert isinstance(ArcGISClient(), ArcGISProviderProtocol), (
        "ArcGISClient does not satisfy ArcGISProviderProtocol. "
        "The protocol must match the concrete client's method signatures."
    )
