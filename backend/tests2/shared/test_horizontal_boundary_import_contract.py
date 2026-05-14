"""Import contracts for newly horizontal backend boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _imports(rel_path: str) -> set[str]:
    tree = ast.parse((_BACKEND_ROOT / rel_path).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize(
    ("rel_path", "forbidden_prefixes"),
    (
        (
            "app/core/connections/rest_sink.py",
            (
                "app.config",
                "requests",
                "httpx",
                "urllib.request",
            ),
        ),
        (
            "app/core/dataframes/tabular.py",
            (
                "app.core.files",
                "app.core.features",
                "app.db",
                "fastapi",
                "fiona",
                "geopandas",
                "httpx",
                "requests",
                "urllib.request",
            ),
        ),
        (
            "app/core/dataframes/ports.py",
            (
                "app.core.files",
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "fiona",
                "geopandas",
                "httpx",
                "pandas",
                "requests",
                "urllib.request",
            ),
        ),
        (
            "app/core/dataframes/default_loader.py",
            (
                "app.core.files",
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "httpx",
                "requests",
                "urllib.request",
            ),
        ),
        (
            "app/core/dataframes/geodata.py",
            (
                "app.core.files",
                "app.core.features",
                "app.db",
                "fastapi",
                "httpx",
                "requests",
                "urllib.request",
            ),
        ),
        (
            "app/core/dataframes/geospatial_io.py",
            (
                "app.core.files",
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "httpx",
                "requests",
                "urllib.request",
            ),
        ),
        (
            "app/core/dataframes/frame_io.py",
            (
                "app.core.files",
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "httpx",
                "requests",
                "urllib.request",
            ),
        ),
        (
            "app/core/dataframes/load_dispatch.py",
            (
                "app.core.files",
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "fiona",
                "geopandas",
                "httpx",
                "requests",
                "urllib.request",
            ),
        ),
        (
            "app/core/dataframes/schema.py",
            (
                "app.core.files",
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "fiona",
                "geopandas",
                "httpx",
                "requests",
                "urllib.request",
            ),
        ),
        (
            "app/core/dataframes/values.py",
            (
                "app.core.files",
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "fiona",
                "geopandas",
                "httpx",
                "requests",
                "urllib.request",
            ),
        ),
        (
            "app/core/dataframes/parallel_ops.py",
            (
                "app.core.files",
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "fiona",
                "geopandas",
                "httpx",
                "requests",
                "urllib.request",
            ),
        ),
        (
            "app/core/scheduler/job_dispatcher.py",
            (
                "app.config",
                "app.db",
                "app.core.features",
                "celery",
                "fastapi",
                "requests",
                "httpx",
            ),
        ),
        (
            "app/core/scheduler/run_archive_store.py",
            (
                "app.core.features",
                "app.core.scheduler.db",
                "app.db",
                "celery",
                "fastapi",
                "requests",
                "httpx",
            ),
        ),
        (
            "app/core/scheduler/runs_catalog.py",
            (
                "app.config",
                "app.core.features",
                "app.core.scheduler.db",
                "app.db",
                "celery",
                "fastapi",
                "requests",
                "httpx",
            ),
        ),
        (
            "app/core/imdf/publish_dispatcher.py",
            (
                "app.config",
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "requests",
                "httpx",
            ),
        ),
        (
            "app/core/admin_system_status/worker_runtime.py",
            (
                "app.core.features",
                "app.db",
                "fastapi",
                "requests",
                "httpx",
            ),
        ),
        (
            "app/core/eseguibili/output_store.py",
            (
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "requests",
                "httpx",
                "urllib.request",
            ),
        ),
        (
            "app/core/results/legacy_file_store.py",
            (
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "requests",
                "httpx",
                "urllib.request",
            ),
        ),
        (
            "app/core/connections/arcgis_sql_helpers.py",
            (
                "app.config",
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "requests",
                "httpx",
                "urllib.request",
            ),
        ),
        (
            "app/core/connections/arcgis_publish_modes.py",
            (
                "app.config",
                "app.core.features",
                "app.db",
                "celery",
                "fastapi",
                "requests",
                "httpx",
                "urllib.request",
            ),
        ),
    ),
)
def test_horizontal_boundaries_do_not_import_forbidden_runtime_modules(
    rel_path: str,
    forbidden_prefixes: tuple[str, ...],
) -> None:
    imports = _imports(rel_path)
    violations = sorted(
        module
        for module in imports
        if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes)
    )
    assert violations == [], f"{rel_path} has forbidden imports: {violations}"
