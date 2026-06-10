"""Contracts for import hygiene cleanup on internal app shims."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests2.shared._ast_identifiers import (
    all_identifiers,
    call_has_keyword,
    function_param_names,
    handler_catches,
    self_attribute_names,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_BACKEND_APP = Path(__file__).resolve().parents[2] / "app"


def _read(relative: str) -> str:
    return (_BACKEND_APP / relative).read_text(encoding="utf-8")


def _handler_catches(handler: ast.ExceptHandler, exc_name: str) -> bool:
    """True if an `except` handler catches `exc_name` (bare or in a tuple)."""
    caught = handler.type
    if caught is None:
        return False
    candidates = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    return any(isinstance(c, ast.Name) and c.id == exc_name for c in candidates)


def _referenced_names(tree: ast.AST) -> set[str]:
    """All identifiers referenced as Name nodes or bound by imports."""
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {alias.asname or alias.name for alias in node.names}
    return names


def test_file_service_does_not_fallback_to_local_model_shims() -> None:
    """files/service must use canonical models with no local fallback shim.

    Checked on the AST (not substring): robust to comments/docstrings/formatting.
    The old substring guard false-failed on any comment mentioning these names
    and false-passed on a reformatted `except (ImportError, ...)`.
    """
    tree = ast.parse(_read("core/files/service.py"))

    classdefs = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "FileSource" not in classdefs, "no local FileSource shim class"
    assert "ColumnInfo" not in classdefs, "no local ColumnInfo shim class"

    catches_import_error = any(
        isinstance(h, ast.ExceptHandler) and _handler_catches(h, "ImportError")
        for h in ast.walk(tree)
    )
    assert not catches_import_error, "no except-ImportError model fallback"

    names = _referenced_names(tree)
    for forbidden in ("_AppFileSource", "_AppColumnInfo", "_USE_APP_MODELS"):
        assert forbidden not in names, f"no {forbidden} shim toggle"


def test_dataframe_schema_uses_canonical_column_info_model() -> None:
    """La sede canonica di ColumnInfo e' app.shared.execution.column_info
    (contratto strutturale, Epic B roadmap accoppiamento): dataframes non
    deve tornare a importarlo dal dominio projects."""
    src = _read("core/dataframes/schema.py")
    tree = ast.parse(src)

    imports_canonical = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "app.shared.execution.column_info"
        and any(alias.name == "ColumnInfo" for alias in n.names)
        for n in ast.walk(tree)
    )
    assert imports_canonical, (
        "schema must import ColumnInfo from app.shared.execution.column_info"
    )

    catches_import_error = any(
        isinstance(h, ast.ExceptHandler) and _handler_catches(h, "ImportError")
        for h in ast.walk(tree)
    )
    assert not catches_import_error, "no except-ImportError fallback"

    # "fallback dataclass" is a prose marker (comment/docstring), inherently
    # textual — keep as a raw-source guard against documenting a shim.
    assert "fallback dataclass" not in src


def test_ticket_metrics_does_not_mask_internal_import_failures() -> None:
    tree = ast.parse(_read("core/admin_system_status/tickets_metrics.py"))

    assert "_TICKETS_IMPORT_ERROR" not in _referenced_names(tree), (
        "no import-error masking sentinel"
    )
    # Broad `except Exception` around the internal import would swallow real
    # failures; AST check also catches `as <any>` / reformatted variants the
    # old `"except Exception as exc"` substring missed.
    masks_exception = any(
        isinstance(h, ast.ExceptHandler) and _handler_catches(h, "Exception")
        for h in ast.walk(tree)
    )
    assert not masks_exception, "no broad except-Exception masking internal imports"


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
    src = _read("core/query_execution/query_executor.py")
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
    identifiers = all_identifiers(src)
    assert "RESULTS_DIR" not in identifiers
    assert "_results_dir" not in identifiers


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

    assert "results_dir" not in all_identifiers(runtime_src)
    # No legacy `settings` parameter threaded through the runtime factories
    # (was the brittle `"settings:" not in src` annotation-substring check).
    assert "settings" not in function_param_names(runtime_src)
    assert not call_has_keyword(_read("app_factory.py"), "build_runtime_services", "settings")
    # Formatting-independent: the old check matched an exact newline+indent.
    assert not call_has_keyword(carica_task_src, "build_orchestrator_stack", "settings")
    assert "results_dir" not in all_identifiers(scheduler_src)
    assert "results_dir" not in all_identifiers(arricchisci_source_src)
    assert not handler_catches(arricchisci_source_src, "ImportError")


def test_arricchisci_and_manipola_no_dead_result_dir_state() -> None:
    """Project sources and API connections resolve through artifact ports."""
    for relative in (
        "core/arricchisci/api_connector.py",
        "core/arricchisci/executor.py",
        "core/manipola/executor.py",
        "core/manipola/project_source_runtime.py",
    ):
        src = _read(relative)
        assert "results_base_dir" not in all_identifiers(src)
        self_attrs = self_attribute_names(src)
        assert "results_dir" not in self_attrs
        assert "data_dir" not in self_attrs


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
