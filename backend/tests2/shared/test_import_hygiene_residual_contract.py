"""Contracts for import hygiene cleanup on internal app shims."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_BACKEND_APP = Path(__file__).resolve().parents[2] / "app"


def _read(relative: str) -> str:
    return (_BACKEND_APP / relative).read_text(encoding="utf-8")


def test_file_service_does_not_fallback_to_local_model_shims() -> None:
    src = _read("core/files/service.py")

    assert "except ImportError" not in src
    assert "class FileSource:" not in src
    assert "class ColumnInfo:" not in src
    assert "_AppFileSource" not in src
    assert "_AppColumnInfo" not in src
    assert "_USE_APP_MODELS" not in src


def test_dataframe_schema_uses_canonical_column_info_model() -> None:
    src = _read("core/dataframes/schema.py")

    assert "from app.core.projects.canvas_models import ColumnInfo" in src
    assert "except ImportError" not in src
    assert "fallback dataclass" not in src


def test_ticket_metrics_does_not_mask_internal_import_failures() -> None:
    src = _read("core/admin_system_status/tickets_metrics.py")

    assert "_TICKETS_IMPORT_ERROR" not in src
    assert "except Exception as exc" not in src


def test_selected_stdlib_imports_are_not_repeated_inside_functions() -> None:
    """Small guard for stdlib imports already cleaned up.

    This is intentionally narrow: optional/heavy dependencies and app.* lazy
    imports are runtime boundaries, not covered by this guard.
    """
    expected_top_level: dict[str, set[str]] = {
        "core/arricchisci/api_connection_rest_file.py": {"re"},
        "core/carica/executor.py": {"time"},
        "core/dataframes/export_writer.py": {"json"},
        "core/dataframes/preview_loader.py": {"json"},
        "core/datasources/ftp.py": {"queue", "threading"},
        "core/datasources/service.py": {"os"},
        "core/datasources/sharepoint.py": {"os", "urllib.parse"},
        "core/eseguibili/db.py": {"datetime"},
        "core/eseguibili/service.py": {"hashlib"},
        "core/estrai/query_filters.py": {"re"},
        "core/manipola/datasource_node_loading.py": {"os"},
        "core/manipola/transforms/spatial.py": {"os"},
    }

    violations: list[tuple[str, int, str]] = []
    for relative, modules in expected_top_level.items():
        source = _read(relative)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            else:
                module = node.names[0].name
            if module not in modules:
                continue
            if not any(child is node for child in tree.body):
                violations.append((relative, node.lineno, module))

    assert not violations


def test_storage_reconciliation_store_has_single_lazy_db_import() -> None:
    """Keep pg_connection lazy, but centralized in one helper."""
    src = _read("core/resources/storage_reconciliation_store.py")
    tree = ast.parse(src)

    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "app.db"
        and any(alias.name == "pg_connection" for alias in node.names)
    ]

    assert len(imports) == 1
    assert "_pg_connection()" in src


def test_query_executor_no_longer_carries_legacy_results_dir() -> None:
    """QueryExecutor persists via result blob ports, not instance result dirs."""
    src = _read("db/query_executor.py")
    tree = ast.parse(src)

    query_executor = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "QueryExecutor"
    )
    init_fn = next(
        node
        for node in query_executor.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    init_args = [arg.arg for arg in init_fn.args.args + init_fn.args.kwonlyargs]

    assert "results_dir" not in init_args
    assert "RESULTS_DIR" not in src
    assert "_results_dir" not in src


def test_runtime_factories_do_not_thread_legacy_results_dirs() -> None:
    """Runtime composition wires executors, not filesystem result roots."""
    runtime_src = _read("runtime/services.py")
    scheduler_src = _read("core/scheduler/execution_manager.py")
    arricchisci_source_src = _read("core/arricchisci/api_connection_source_loading.py")
    carica_task_src = (
        Path(__file__).resolve().parents[2]
        / "workers"
        / "tasks"
        / "carica_task.py"
    ).read_text(encoding="utf-8")

    assert "results_dir" not in runtime_src
    assert "settings:" not in runtime_src
    assert "build_runtime_services(settings=" not in _read("app_factory.py")
    assert "build_orchestrator_stack(\n            settings=" not in carica_task_src
    assert "results_dir=" not in scheduler_src
    assert "results_dir=" not in arricchisci_source_src
    assert "except ImportError" not in arricchisci_source_src


def test_arricchisci_and_manipola_no_dead_result_dir_state() -> None:
    """Project sources and API connections resolve through artifact ports."""
    for relative in (
        "core/arricchisci/api_connector.py",
        "core/arricchisci/executor.py",
        "core/manipola/executor.py",
        "core/manipola/project_source_runtime.py",
    ):
        src = _read(relative)
        assert "results_base_dir" not in src
        assert "self.results_dir" not in src
        assert "self.data_dir" not in src


def test_selected_light_app_imports_are_not_repeated_inside_functions() -> None:
    """Guard app imports that were duplicated and are not optional boundaries."""
    expected_top_level: dict[str, set[str]] = {
        "core/arricchisci/_rate_limiter.py": {"app.config"},
        "core/carica/conflict_request_loader.py": {
            "app.core.dataframes.tabular"
        },
        "observability/health.py": {"app.config"},
    }

    violations: list[tuple[str, int, str]] = []
    for relative, modules in expected_top_level.items():
        source = _read(relative)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            else:
                module = node.names[0].name
            if module not in modules:
                continue
            if not any(child is node for child in tree.body):
                violations.append((relative, node.lineno, module))

    assert not violations
