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
ARCGIS_REST_WRITE = APP_DIR / "core" / "features" / "arcgis_rest_write.py"
ARCGIS_FACTORY = APP_DIR / "core" / "connections" / "arcgis_provider_factory.py"
ARCGIS_RESOLVER = APP_DIR / "core" / "connections" / "arcgis_connection_resolver.py"
CARICA_TASK = WORKERS_DIR / "tasks" / "carica_task.py"
CARICA_ARCGIS_BOUNDARY = APP_DIR / "core" / "carica" / "arcgis_write_boundary.py"

NEUTRAL_FACTORY_MODULE = "app.core.connections.arcgis_provider_factory"
NEUTRAL_RESOLVER_MODULE = "app.core.connections.arcgis_connection_resolver"
ARCGIS_SQL_HELPERS_MODULE = "app.core.connections.arcgis_sql_helpers"
ARCGIS_PUBLISH_MODES_MODULE = "app.core.connections.arcgis_publish_modes"
ARCGIS_SERVICE_DISCOVERY_MODULE = "app.core.connections.arcgis_service_discovery"
ARCGIS_SERVICE_LIFECYCLE_MODULE = "app.core.connections.arcgis_service_lifecycle"
ARCGIS_WRITER_BOUNDARY_MODULE = "app.core.connections.arcgis_writer_boundary"
SHARED_PROVIDER_MODULE = "app.shared.connections.arcgis_provider"
SHARED_CONNECTION_MODULE = "app.shared.connections.arcgis_connection"


def _rel(path: Path) -> str:
    return path.relative_to(BACKEND_DIR).as_posix()


# Accepted-debt allowlist for `test_direct_arcgis_rest_is_confined_to_concrete_provider_client`.
#
# These are the residual ArcGIS REST callsites that still live outside
# `arcgis_client.py` / `arcgis_rest_write.py`. The boundary contract is
# clear (REST mechanics belong on the concrete client), but a clean
# extraction of these ~22 callsites is a dedicated refactor — moving each
# `requests.get(.../sharing/rest/...)` / `client._http(...)` call into a
# named client method and updating the caller. Until that refactor lands,
# we ratchet shrink-only: the test fails if NEW violations appear or if
# entries here are refactored away (and so should be trimmed).
_ACCEPTED_REST_DEBT: dict[str, set[tuple[int, str]]] = {
    "app/core/features/arcgis_auth.py": {
        (206, "._http"),
        (210, "._http"),
        (364, "/sharing/rest"),
        (446, "._http"),
        (526, "/sharing/rest"),
        (532, "/sharing/rest"),
        (533, "/sharing/rest"),
        (548, "/sharing/rest"),
        (586, "._http"),
    },
    "app/core/features/arcgis_feature_write_methods.py": {
        (304, "/rest/admin"),
        (377, "._http"),
    },
    "app/core/features/arcgis_read.py": {
        (280, "._http"),
        (333, "._http"),
    },
    "app/core/features/arcgis_service_discovery.py": {
        (201, "/sharing/rest"),
        (376, "/sharing/rest"),
        (439, "/sharing/rest"),
        (461, "createFolder"),
        (492, "/sharing/rest"),
    },
}


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
    if src_file in {ARCGIS_CLIENT, ARCGIS_REST_WRITE}:
        return

    violations = set(scan_arcgis_rest_mechanics(src_file))
    expected = _ACCEPTED_REST_DEBT.get(_rel(src_file), set())

    new_violations = violations - expected
    assert not new_violations, (
        f"{_rel(src_file)}: NEW direct ArcGIS REST/HTTP mechanics outside the "
        f"concrete provider client boundary (not in accepted-debt allowlist): "
        f"{sorted(new_violations)}. Either move the call into "
        f"app/core/features/arcgis_client.py / arcgis_rest_write.py or, if the "
        f"refactor is staged, add the entry to `_ACCEPTED_REST_DEBT` with a "
        f"tracking note."
    )

    obsolete = expected - violations
    assert not obsolete, (
        f"{_rel(src_file)}: `_ACCEPTED_REST_DEBT` lists entries that have been "
        f"refactored away: {sorted(obsolete)}. Trim them so the ratchet keeps "
        f"shrinking."
    )


@pytest.mark.parametrize(
    "src_file",
    python_files_under(IMDF_DIR),
    ids=lambda p: p.relative_to(BACKEND_DIR).as_posix(),
)
def test_imdf_uses_arcgis_provider_protocol_resolver_or_neutral_helpers(
    src_file: Path,
) -> None:
    allowed_connections_imports = {
        NEUTRAL_RESOLVER_MODULE,
        ARCGIS_SQL_HELPERS_MODULE,
        ARCGIS_PUBLISH_MODES_MODULE,
        ARCGIS_SERVICE_DISCOVERY_MODULE,
        ARCGIS_SERVICE_LIFECYCLE_MODULE,
        ARCGIS_WRITER_BOUNDARY_MODULE,
    }
    rel = _rel(src_file)

    forbidden_imports = set()
    for module in imports_in_file(src_file):
        if module.startswith("app.core.features"):
            forbidden_imports.add(module)
        elif (
            module.startswith("app.core.connections")
            and module not in allowed_connections_imports
        ):
            forbidden_imports.add(module)

    assert not forbidden_imports, (
        f"{rel}: IMDF must use shared ArcGIS provider protocols or "
        f"the neutral resolver only; forbidden imports: {sorted(forbidden_imports)}."
    )


def test_carica_arcgis_path_uses_provider_writer_boundary() -> None:
    imports = set(imports_in_file(CARICA_TASK))
    boundary_imported_names = set(imported_names_in_file(CARICA_ARCGIS_BOUNDARY))

    assert any(
        module == "app.core.connections.arcgis_writer_boundary"
        and name == "ArcGISFeatureWriter"
        for module, name, _line in boundary_imported_names
    )
    assert "app.core.features.arcgis_client" not in imports
    assert NEUTRAL_FACTORY_MODULE not in imports
    assert NEUTRAL_RESOLVER_MODULE not in imports
