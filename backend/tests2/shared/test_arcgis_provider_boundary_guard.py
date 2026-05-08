"""
Architecture guard - ADR-0023: IMDF publish domain must not import

from the Features connection-management domain (app.core.features.*).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared]

_IMDF_DIR = Path(__file__).parents[2] / "app" / "core" / "imdf"
_SHARED_CONNECTIONS_DIR = Path(__file__).parents[2] / "app" / "shared" / "connections"
_BACKEND_DIR = Path(__file__).parents[2]

_FORBIDDEN_PREFIXES = ("app.core.features",)
_SHARED_CONNECTIONS_FORBIDDEN_PREFIXES = ("app.core", "app.db", "workers")

_ARCGIS_REST_MECHANICS_PATTERNS = (
    "._http",
    "_request_with_token",
    "/sharing/rest",
    "/rest/admin",
    "createFolder",
    "permanentDelete",
    "updateDefinition",
)
_ARCGIS_REST_MECHANICS_ROOTS = (
    _BACKEND_DIR / "app",
    _BACKEND_DIR / "workers",
)
_ARCGIS_REST_MECHANICS_ALLOWED_FILES = {
    _BACKEND_DIR / "app" / "core" / "features" / "arcgis_client.py",
}
_LEGACY_ARCGIS_ADAPTER_MODULE = ".".join(
    ("app.core.features", "arcgis_connection_adapter")
)
_LEGACY_ARCGIS_ADAPTER_ALLOWED_FILES = {
    _BACKEND_DIR / "app" / "core" / "features" / "arcgis_connection_adapter.py",
}
_PRODUCTION_ARCGIS_FACTORY_IMPORT_ROOTS = (
    _BACKEND_DIR / "app",
    _BACKEND_DIR / "workers",
)
_LEGACY_CREATE_CLIENT_IMPORTS = {
    ("app.core.features", "create_client"),
    ("app.core.features.arcgis_client", "create_client"),
}
_LEGACY_ESRI_CONVERSION_IMPORTS = {
    ("app.core.features.arcgis_client", "EsriJsonConverter"),
    ("app.core.features.arcgis_client", "EsriTypeMapper"),
}
_LEGACY_CREATE_CLIENT_IMPORT_ALLOWLIST = {
    (
        _BACKEND_DIR / "app" / "core" / "features" / "__init__.py"
    ): "KEEP_COMPAT: package-level legacy re-export for existing importers.",
}

# Known violations - TODO: fix these per macro-components.md section 2.3.
# Each entry is (filename -> exact imported modules). The guard is strict
# on all other files and on any new module inside known-bad files.
# Remove an entry only when the cross-domain import is gone.
_KNOWN_VIOLATIONS: dict[str, set[str]] = {}

_KNOWN_VIOLATION_REASONS: dict[str, str] = {}


def _imdf_python_files() -> list[Path]:
    return sorted(_IMDF_DIR.rglob("*.py"))


def _shared_connections_python_files() -> list[Path]:
    return sorted(_SHARED_CONNECTIONS_DIR.rglob("*.py"))


def _arcgis_rest_mechanics_python_files() -> list[Path]:
    files: list[Path] = []
    for root in _ARCGIS_REST_MECHANICS_ROOTS:
        files.extend(root.rglob("*.py"))
    return sorted(files)


def _production_arcgis_factory_import_python_files() -> list[Path]:
    files: list[Path] = []
    for root in _PRODUCTION_ARCGIS_FACTORY_IMPORT_ROOTS:
        files.extend(root.rglob("*.py"))
    return sorted(files)


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


def _imported_names_in_file(path: Path) -> list[tuple[str, str, int]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    imported_names: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported_names.append((node.module, alias.name, node.lineno))
    return imported_names


def _scan_arcgis_rest_mechanics(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [(exc.lineno or 0, f"syntax error: {exc}")]

    violations: list[tuple[int, str]] = []
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def is_dict_key(node: ast.AST) -> bool:
        parent = parents.get(node)
        return isinstance(parent, ast.Dict) and node in parent.keys

    def scan_node(node: ast.AST) -> None:
        if isinstance(node, ast.Attribute) and node.attr == "_http":
            violations.append((node.lineno, "._http"))
        elif isinstance(node, ast.Name) and node.id == "_request_with_token":
            violations.append((node.lineno, "_request_with_token"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for pattern in _ARCGIS_REST_MECHANICS_PATTERNS:
                if pattern == "._http":
                    continue
                if pattern in ("/sharing/rest", "/rest/admin") and pattern in node.value:
                    violations.append((node.lineno, pattern))
                elif pattern in {"createFolder", "permanentDelete", "updateDefinition"} and (
                    node.value == pattern
                    or f"/{pattern}" in node.value
                    or (is_dict_key(node) and pattern in node.value)
                ):
                    violations.append((node.lineno, pattern))

        for child in ast.iter_child_nodes(node):
            scan_node(child)

    def scan_body(body: list[ast.stmt]) -> None:
        statements = body
        if (
            statements
            and isinstance(statements[0], ast.Expr)
            and isinstance(statements[0].value, ast.Constant)
            and isinstance(statements[0].value.value, str)
        ):
            statements = statements[1:]
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if isinstance(statement, ast.ClassDef):
                    for part in [*statement.bases, *statement.keywords, *statement.decorator_list]:
                        scan_node(part)
                else:
                    parts: list[ast.AST] = [statement.args, *statement.decorator_list]
                    if statement.returns is not None:
                        parts.append(statement.returns)
                    for part in parts:
                        scan_node(part)
                scan_body(statement.body)
            else:
                scan_node(statement)

    scan_body(tree.body)
    return violations


@pytest.mark.parametrize("src_file", _shared_connections_python_files(), ids=lambda p: p.name)
def test_shared_connections_remains_leaf_like(src_file: Path) -> None:
    """Shared connection contracts must not depend on app core/db/workers."""
    for module in _imports_in_file(src_file):
        for prefix in _SHARED_CONNECTIONS_FORBIDDEN_PREFIXES:
            assert not module.startswith(prefix), (
                f"{src_file.name}: app.shared.connections must remain leaf-like; "
                f"forbidden import {module!r} starts with {prefix!r}."
            )


@pytest.mark.parametrize(
    "src_file",
    _arcgis_rest_mechanics_python_files(),
    ids=lambda p: p.relative_to(_BACKEND_DIR).as_posix(),
)
def test_direct_arcgis_rest_mechanics_stay_inside_provider_client(src_file: Path) -> None:
    """Direct ArcGIS REST mechanics belong in the provider/client boundary.

    IMDF and workers must call provider methods instead of constructing portal
    or admin REST calls, touching the injected HTTP client, or invoking the
    private token-aware request helper directly.
    """
    if src_file in _ARCGIS_REST_MECHANICS_ALLOWED_FILES:
        return

    violations = _scan_arcgis_rest_mechanics(src_file)

    assert not violations, (
        f"{src_file.relative_to(_BACKEND_DIR).as_posix()}: direct ArcGIS REST "
        "mechanics detected outside the provider/client boundary. "
        f"Violations: {violations}. "
        "Use app.core.features.arcgis_client.ArcGISClient provider methods "
        "instead of direct REST/HTTP mechanics. If a provider module is being "
        "intentionally introduced, add a narrow allowlist entry with rationale."
    )


@pytest.mark.parametrize(
    "src_file",
    _arcgis_rest_mechanics_python_files(),
    ids=lambda p: p.relative_to(_BACKEND_DIR).as_posix(),
)
def test_legacy_arcgis_connection_adapter_is_only_the_wrapper(src_file: Path) -> None:
    """The neutral resolver is the canonical ArcGIS connection entry point."""
    if src_file in _LEGACY_ARCGIS_ADAPTER_ALLOWED_FILES:
        return

    imports = _imports_in_file(src_file)

    assert _LEGACY_ARCGIS_ADAPTER_MODULE not in imports, (
        f"{src_file.relative_to(_BACKEND_DIR).as_posix()}: legacy ArcGIS "
        "connection adapter imported outside its compatibility wrapper. "
        "Import app.core.connections.arcgis_connection_resolver instead."
    )


def test_legacy_create_client_import_allowlist_reasons_are_explicit() -> None:
    """Allowlisted legacy factory imports must document why they remain."""
    for path, reason in _LEGACY_CREATE_CLIENT_IMPORT_ALLOWLIST.items():
        assert reason.startswith("KEEP_COMPAT:"), (
            f"{path.relative_to(_BACKEND_DIR).as_posix()}: legacy "
            "create_client import allowlist entries require an explicit "
            "KEEP_COMPAT reason."
        )


@pytest.mark.parametrize(
    "src_file",
    _production_arcgis_factory_import_python_files(),
    ids=lambda p: p.relative_to(_BACKEND_DIR).as_posix(),
)
def test_production_imports_neutral_arcgis_provider_factory(src_file: Path) -> None:
    """Production modules must not import the legacy concrete-client factory.

    This scans only import statements via AST, so comments and docstrings that
    mention ``create_client`` do not produce noisy failures. Direct concrete
    ``ArcGISClient`` imports are still allowed for legacy code and tests that
    exercise the concrete client.
    """
    if src_file in _LEGACY_CREATE_CLIENT_IMPORT_ALLOWLIST:
        return

    violations = [
        (module, name, line)
        for module, name, line in _imported_names_in_file(src_file)
        if (module, name) in _LEGACY_CREATE_CLIENT_IMPORTS
    ]

    assert not violations, (
        f"{src_file.relative_to(_BACKEND_DIR).as_posix()}: production modules "
        "must import the canonical ArcGIS provider factory from "
        "app.core.connections.arcgis_provider_factory, not create_client from "
        "app.core.features.arcgis_client. "
        f"Violations: {violations}. "
        "If this is a required legacy compatibility re-export, add a narrow "
        "allowlist entry with a KEEP_COMPAT reason."
    )


@pytest.mark.parametrize(
    "src_file",
    _production_arcgis_factory_import_python_files(),
    ids=lambda p: p.relative_to(_BACKEND_DIR).as_posix(),
)
def test_production_imports_esri_conversion_from_shared(src_file: Path) -> None:
    """ESRI type/JSON conversion canonical imports live under shared connections."""
    violations = [
        (module, name, line)
        for module, name, line in _imported_names_in_file(src_file)
        if (module, name) in _LEGACY_ESRI_CONVERSION_IMPORTS
    ]

    assert not violations, (
        f"{src_file.relative_to(_BACKEND_DIR).as_posix()}: production modules "
        "must import EsriJsonConverter/EsriTypeMapper from "
        "app.shared.connections, not app.core.features.arcgis_client. "
        f"Violations: {violations}. arcgis_client keeps these only as "
        "KEEP_COMPAT re-exports."
    )


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

    if allowed_known:
        assert seen_known == allowed_known, (
            f"{src_file.name}: known ADR-0023 residual allowlist drift. "
            f"Expected {sorted(allowed_known)}, saw {sorted(seen_known)}. "
            "If the import was removed, trim _KNOWN_VIOLATIONS. "
            f"Residual rationale: {_KNOWN_VIOLATION_REASONS[src_file.name]}"
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
    for method in (
        "count_features_where",
        "is_service_name_available",
        "list_layers",
        "get_layer_schema",
    ):
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


def test_esri_conversion_canonical_imports_from_shared_connections():
    """Provider-neutral ESRI conversion helpers are canonical in shared."""
    from app.shared.connections.esri_json import EsriJsonConverter
    from app.shared.connections.esri_types import EsriTypeMapper

    assert callable(EsriJsonConverter.features_to_rows)
    assert callable(EsriTypeMapper.map_field_type)


def test_esri_conversion_backward_compat_via_arcgis_client():
    """arcgis_client.py keeps KEEP_COMPAT re-exports for legacy importers."""
    from app.core.features.arcgis_client import EsriJsonConverter as JsonFromClient
    from app.core.features.arcgis_client import EsriTypeMapper as TypeFromClient
    from app.shared.connections.esri_json import EsriJsonConverter as JsonFromShared
    from app.shared.connections.esri_types import EsriTypeMapper as TypeFromShared

    assert JsonFromClient is JsonFromShared
    assert TypeFromClient is TypeFromShared


def test_arcgis_connection_resolver_uses_neutral_provider_factory():
    """Resolver must not import the legacy feature-owned provider factory."""
    resolver_path = (
        _BACKEND_DIR / "app" / "core" / "connections" / "arcgis_connection_resolver.py"
    )

    imports = _imports_in_file(resolver_path)

    assert "app.core.features.arcgis_client" not in imports, (
        "arcgis_connection_resolver must not import provider factory from "
        "app.core.features.arcgis_client. Use the neutral connection factory."
    )
    assert "app.core.connections.arcgis_provider_factory" in imports


def test_arcgis_provider_factory_is_neutral_canonical_import():
    """New provider construction belongs under app.core.connections."""
    from app.core.connections.arcgis_provider_factory import create_arcgis_provider
    from app.shared.connections.arcgis_provider import ArcGISProviderProtocol

    assert isinstance(create_arcgis_provider(), ArcGISProviderProtocol)


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
