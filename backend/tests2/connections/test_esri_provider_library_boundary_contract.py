"""Focused ESRI provider library-like boundary contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests2.shared.esri_boundary_helpers import (
    BACKEND_DIR,
    IMDF_DIR,
    SHARED_CONNECTIONS_DIR,
    imported_names_in_file,
    imports_in_file,
    python_files_under,
    scan_arcgis_rest_mechanics,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]

APP_DIR = BACKEND_DIR / "app"
WORKERS_DIR = BACKEND_DIR / "workers"
ARCGIS_CLIENT = APP_DIR / "core" / "features" / "arcgis_client.py"
ARCGIS_FACTORY = APP_DIR / "core" / "connections" / "arcgis_provider_factory.py"
ARCGIS_RESOLVER = APP_DIR / "core" / "connections" / "arcgis_connection_resolver.py"
CARICA_TASK = WORKERS_DIR / "tasks" / "carica_task.py"

NEUTRAL_FACTORY_MODULE = "app.core.connections.arcgis_provider_factory"
NEUTRAL_RESOLVER_MODULE = "app.core.connections.arcgis_connection_resolver"
SHARED_PROVIDER_MODULE = "app.shared.connections.arcgis_provider"
SHARED_CONNECTION_MODULE = "app.shared.connections.arcgis_connection"


def _rel(path: Path) -> str:
    return path.relative_to(BACKEND_DIR).as_posix()


def test_arcgis_factory_and_resolver_are_canonical_neutral_paths() -> None:
    factory_imports = imports_in_file(ARCGIS_FACTORY)
    resolver_imports = imports_in_file(ARCGIS_RESOLVER)

    assert SHARED_PROVIDER_MODULE in factory_imports
    assert SHARED_PROVIDER_MODULE in resolver_imports
    assert SHARED_CONNECTION_MODULE in resolver_imports
    assert NEUTRAL_FACTORY_MODULE in resolver_imports
    assert "app.core.features.arcgis_client" not in resolver_imports


@pytest.mark.parametrize(
    "src_file",
    python_files_under(SHARED_CONNECTIONS_DIR),
    ids=lambda p: p.relative_to(BACKEND_DIR).as_posix(),
)
def test_shared_connections_package_is_leaf_like(src_file: Path) -> None:
    forbidden_prefixes = ("app.core", "app.db", "workers")
    violations = [
        module
        for module in imports_in_file(src_file)
        for prefix in forbidden_prefixes
        if module.startswith(prefix)
    ]

    assert not violations, (
        f"{_rel(src_file)}: app.shared.connections is the ESRI contract leaf; "
        f"forbidden imports detected: {violations}."
    )


@pytest.mark.parametrize(
    "src_file",
    python_files_under(APP_DIR, WORKERS_DIR),
    ids=lambda p: p.relative_to(BACKEND_DIR).as_posix(),
)
def test_direct_arcgis_rest_is_confined_to_concrete_provider_client(src_file: Path) -> None:
    if src_file == ARCGIS_CLIENT:
        return

    violations = scan_arcgis_rest_mechanics(src_file)

    assert not violations, (
        f"{_rel(src_file)}: direct ArcGIS REST/HTTP mechanics are outside the "
        f"concrete provider client boundary: {violations}."
    )


@pytest.mark.parametrize(
    "src_file",
    python_files_under(IMDF_DIR),
    ids=lambda p: p.relative_to(BACKEND_DIR).as_posix(),
)
def test_imdf_uses_arcgis_provider_protocol_or_resolver_only(src_file: Path) -> None:
    allowed_connections_imports = {
        NEUTRAL_RESOLVER_MODULE,
    }
    forbidden_imports = {
        module
        for module in imports_in_file(src_file)
        if module.startswith("app.core.features")
        or (
            module.startswith("app.core.connections")
            and module not in allowed_connections_imports
        )
    }

    assert not forbidden_imports, (
        f"{_rel(src_file)}: IMDF must use shared ArcGIS provider protocols or "
        f"the neutral resolver only; forbidden imports: {sorted(forbidden_imports)}."
    )


def test_carica_arcgis_path_uses_provider_writer_boundary() -> None:
    imports = set(imports_in_file(CARICA_TASK))
    imported_names = set(imported_names_in_file(CARICA_TASK))

    assert any(
        module == "app.core.features.arcgis_writer_boundary"
        and name == "ArcGISFeatureWriter"
        for module, name, _line in imported_names
    )
    assert "app.core.features.arcgis_client" not in imports
    assert NEUTRAL_FACTORY_MODULE not in imports
    assert NEUTRAL_RESOLVER_MODULE not in imports
