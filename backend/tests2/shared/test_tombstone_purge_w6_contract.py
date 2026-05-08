"""Tombstone purge W6 anti-resurrection guards.

These guards are intentionally source-based. They do not import production
modules, so concurrent facade deletions should ratchet the baseline down
instead of breaking the scanner itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_PROD_ROOTS = (_BACKEND_ROOT / "app", _BACKEND_ROOT / "workers")

_FORBIDDEN_PRODUCTION_IMPORT_PREFIXES = (
    "app.services",
    "app.db.config_db_service",
    "app.db.results_db_service",
    "app.shared.exceptions",
)

# Current plain KEEP_COMPAT markers are active debt. This baseline is
# shrink-only: removed or upgraded markers pass, but any new plain marker must
# use KEEP_COMPAT_WITH_REMOVAL_CONDITION or be deliberately registered here.
_KNOWN_PLAIN_KEEP_COMPAT_MARKERS = frozenset(
    {
        "app/core/arricchisci/api_connection_rest_file.py::(KEEP_COMPAT).",
        "app/core/arricchisci/api_connector.py::\"\"\"KEEP_COMPAT wrapper: delega a",
        "app/core/arricchisci/canvas_orchestrator.py::KEEP_COMPAT su `svc._current_group_id` /",
        "app/core/arricchisci/canvas_orchestrator.py::classe come wrapper thin per KEEP_COMPAT.",
        "app/core/arricchisci/canvas_orchestrator.py::ctx. La firma con `svc` resta per KEEP_COMPAT con il",
        "app/core/arricchisci/execution_context.py::\"\"\"KEEP_COMPAT thin subclass of `CanvasExecutionContext`.",
        "app/core/auth/auth.py::# KEEP_COMPAT: gli alias `_rate_lock`, `_failed_attempts`",
        "app/core/auth/auth.py::\"\"\"KEEP_COMPAT wrapper: legge le tre costanti monkeypatch-ate a",
        "app/core/auth/auth.py::from app.core.auth.owner_dependencies import (  # noqa: F401 (compatibility export KEEP_COMPAT)",
        "app/core/features/arcgis_client.py::`{success, feature_count, errors}` (KEEP_COMPAT per",
        "app/core/features/arcgis_client.py::delegate `KEEP_COMPAT` per i caller `upload_imdf_layers` che",
        "app/core/features/models.py::KEEP_COMPAT: this service_url + layer_id shape is no longer used by the",
        "app/core/imdf/arcgis/publish_ops.py::thin `KEEP_COMPAT` wrapper to:",
        "app/core/imdf/arcgis/service_discovery.py::\"\"\"KEEP_COMPAT: IMDF wrapper over the generic ArcGIS folder boundary.",
        "app/core/imdf/arcgis/service_discovery.py::\"\"\"KEEP_COMPAT: read-only IMDF folder-list wrapper.",
        "app/core/imdf/arcgis/service_discovery.py::KEEP_COMPAT: IMDF wrapper over `client.list_user_items`; only",
        "app/core/imdf/arcgis/service_discovery.py::KEEP_COMPAT: fail-open IMDF wrapper around",
        "app/core/imdf/arcgis/sql_helpers.py::KEEP_COMPAT: thin IMDF wrapper for legacy publish call sites. New",
        "app/core/imdf/validator.py::KEEP_COMPAT: this module-level symbol remains as a thin wrapper for:",
        "app/utils/response_parser.py::\"\"\"KEEP_COMPAT wrapper for Arricchisci response parsing.",
    }
)


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _iter_prod_python() -> list[Path]:
    paths: list[Path] = []
    for root in _PROD_ROOTS:
        if root.exists():
            paths.extend(root.rglob("*.py"))
    return sorted(paths)


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _import_edges(tree: ast.AST) -> set[str]:
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.update(alias.name for alias in node.names)
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            edges.add(module)
            edges.update(f"{module}.{alias.name}" for alias in node.names)
    return edges


def _scan_forbidden_production_imports(prefix: str) -> list[str]:
    hits: list[str] = []
    for path in _iter_prod_python():
        tree = _parse(path)
        if tree is None:
            continue
        for edge in sorted(_import_edges(tree)):
            if edge == prefix or edge.startswith(f"{prefix}."):
                hits.append(f"{_rel(path)} -> {edge}")
    return sorted(hits)


def _plain_keep_compat_markers() -> set[str]:
    markers: set[str] = set()
    for path in _iter_prod_python():
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        rel = _rel(path)
        for line in lines:
            stripped = line.strip()
            if "KEEP_COMPAT" not in stripped:
                continue
            if "KEEP_COMPAT_WITH_REMOVAL_CONDITION" in stripped:
                continue
            markers.add(f"{rel}::{stripped}")
    return markers


@pytest.mark.parametrize("prefix", _FORBIDDEN_PRODUCTION_IMPORT_PREFIXES)
def test_tombstoned_production_imports_do_not_resurrect(prefix: str):
    hits = _scan_forbidden_production_imports(prefix)

    assert not hits, (
        f"Production code imports tombstoned compatibility surface `{prefix}`.\n"
        "Use the domain-owned canonical module instead.\nViolations:\n  - "
        + "\n  - ".join(hits)
    )


def test_no_new_plain_keep_compat_markers_without_removal_condition():
    current = _plain_keep_compat_markers()
    new_markers = sorted(current - _KNOWN_PLAIN_KEEP_COMPAT_MARKERS)

    assert not new_markers, (
        "New KEEP_COMPAT markers must include an explicit removal condition. "
        "Use `KEEP_COMPAT_WITH_REMOVAL_CONDITION: remove after ...` or add a "
        "deliberate shrink-only ledger entry with owner/context.\nNew markers:\n  - "
        + "\n  - ".join(new_markers)
    )
