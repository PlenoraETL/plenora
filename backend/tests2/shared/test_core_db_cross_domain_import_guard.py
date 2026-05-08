"""Guard core domain DB modules against importing another domain DB module."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_CORE_ROOT = _BACKEND_ROOT / "app" / "core"
_ALLOWED_SEVERITIES = frozenset({"HIGH", "MEDIUM", "LOW"})
_ALLOWED_RESIDUAL_STATUSES = (
    "KEEP_COMPAT_WITH_REMOVAL_CONDITION:",
    "BLOCKER:",
)


@dataclass(frozen=True)
class CrossDomainDbEdge:
    src_path: str
    src_domain: str
    imported_domain: str
    imported_module: str
    imported_name: str
    line: int


@dataclass(frozen=True)
class AllowedCrossDomainDbEdge:
    src_path: str
    imported_module: str
    imported_name: str
    severity: str
    reason: str


@dataclass(frozen=True)
class RemovedCrossDomainDbEdge:
    src_path: str
    imported_module: str
    imported_name: str
    reason: str


_ALLOWED_CROSS_DOMAIN_DB_EDGES: tuple[AllowedCrossDomainDbEdge, ...] = ()
_REMOVED_CROSS_DOMAIN_DB_EDGES: tuple[RemovedCrossDomainDbEdge, ...] = (
    RemovedCrossDomainDbEdge(
        src_path="app/core/auth/db.py",
        imported_module="app.core.intermediate.db",
        imported_name="delete_all_runs_for_canvas",
        reason=(
            "REMOVED: auth group cleanup now delegates intermediate run "
            "cleanup through the application cleanup registry boundary"
        ),
    ),
    RemovedCrossDomainDbEdge(
        src_path="app/core/intermediate/db.py",
        imported_module="app.core.projects.db",
        imported_name="get_project_retention_n",
        reason=(
            "REMOVED: intermediate retention no longer reads "
            "project-owned retention policy from the projects DB leaf"
        ),
    ),
)


def _domain_db_files() -> list[Path]:
    return sorted(path for path in _CORE_ROOT.glob("*/db.py") if path.is_file())


def _domain_for_db_file(path: Path) -> str:
    return path.parent.name


def _edge_key(
    edge: CrossDomainDbEdge | AllowedCrossDomainDbEdge | RemovedCrossDomainDbEdge,
) -> tuple[str, str, str]:
    return (edge.src_path, edge.imported_module, edge.imported_name)


def _module_domain(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) == 4 and parts[:2] == ["app", "core"] and parts[3] == "db":
        return parts[2]
    return None


def _iter_cross_domain_db_edges(path: Path) -> list[CrossDomainDbEdge]:
    src_domain = _domain_for_db_file(path)
    src_rel = path.relative_to(_BACKEND_ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    edges: list[CrossDomainDbEdge] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_domain = _module_domain(node.module)
            if imported_domain is None or imported_domain == src_domain:
                continue
            for alias in node.names:
                edges.append(
                    CrossDomainDbEdge(
                        src_path=src_rel,
                        src_domain=src_domain,
                        imported_domain=imported_domain,
                        imported_module=node.module,
                        imported_name=alias.name,
                        line=node.lineno,
                    )
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_domain = _module_domain(alias.name)
                if imported_domain is None or imported_domain == src_domain:
                    continue
                edges.append(
                    CrossDomainDbEdge(
                        src_path=src_rel,
                        src_domain=src_domain,
                        imported_domain=imported_domain,
                        imported_module=alias.name,
                        imported_name="<module>",
                        line=node.lineno,
                    )
                )

    return edges


def _current_cross_domain_db_edges() -> list[CrossDomainDbEdge]:
    edges: list[CrossDomainDbEdge] = []
    for path in _domain_db_files():
        edges.extend(_iter_cross_domain_db_edges(path))
    return sorted(edges, key=lambda edge: _edge_key(edge))


def test_core_domain_db_modules_do_not_import_other_domain_db_modules() -> None:
    """Domain DB modules should not call each other directly.

    Cross-domain persistence coordination belongs in service/application
    boundaries. Any temporary DB-to-DB edge must be named here with severity
    and a concrete removal condition.
    """
    allowed_by_key = {
        _edge_key(edge): edge for edge in _ALLOWED_CROSS_DOMAIN_DB_EDGES
    }
    current_edges = _current_cross_domain_db_edges()
    violations = [
        edge for edge in current_edges if _edge_key(edge) not in allowed_by_key
    ]

    assert not violations, (
        "New cross-domain core DB import detected. Domain DB modules must not "
        "import another app.core.<domain>.db directly; move coordination to a "
        "service/application boundary or add a narrow allowlist entry with "
        f"severity and removal condition. Violations: {violations}"
    )

    seen_keys = {_edge_key(edge) for edge in current_edges}
    stale_allowed = [
        edge for edge in _ALLOWED_CROSS_DOMAIN_DB_EDGES if _edge_key(edge) not in seen_keys
    ]
    assert not stale_allowed, (
        "Cross-domain DB allowlist drift detected. Remove stale allowed edges: "
        f"{stale_allowed}"
    )


def test_cross_domain_db_import_allowlist_has_explicit_severity_and_removal() -> None:
    assert not (
        {_edge_key(edge) for edge in _ALLOWED_CROSS_DOMAIN_DB_EDGES}
        & {_edge_key(edge) for edge in _REMOVED_CROSS_DOMAIN_DB_EDGES}
    )

    for edge in _ALLOWED_CROSS_DOMAIN_DB_EDGES:
        assert edge.severity in _ALLOWED_SEVERITIES, (
            f"{edge.src_path} -> {edge.imported_module}: invalid severity "
            f"{edge.severity!r}; use HIGH, MEDIUM, or LOW."
        )
        assert edge.reason.startswith(_ALLOWED_RESIDUAL_STATUSES), (
            f"{edge.src_path} -> {edge.imported_module}: allowlist reason must "
            "start with KEEP_COMPAT_WITH_REMOVAL_CONDITION: or BLOCKER: and "
            "state the removal path."
        )

    for edge in _REMOVED_CROSS_DOMAIN_DB_EDGES:
        assert edge.reason.startswith("REMOVED:"), edge


def test_removed_cross_domain_db_import_tombstones_stay_absent() -> None:
    current_by_key = {
        _edge_key(edge): edge for edge in _current_cross_domain_db_edges()
    }
    reintroduced = [
        edge
        for edge in _REMOVED_CROSS_DOMAIN_DB_EDGES
        if _edge_key(edge) in current_by_key
    ]

    assert not reintroduced, (
        "Removed DB-to-DB imports must stay tombstoned. Reintroduced imports: "
        f"{reintroduced}"
    )


def test_auth_db_does_not_import_intermediate_db() -> None:
    src = (_CORE_ROOT / "auth" / "db.py").read_text(encoding="utf-8")

    assert "app.core.intermediate.db" not in src
    assert "delete_all_runs_for_canvas" not in src


def test_intermediate_db_facade_does_not_import_projects_db() -> None:
    src = (_CORE_ROOT / "intermediate" / "db.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported_modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
            imported_modules.update(
                f"{node.module}.{alias.name}" for alias in node.names
            )
        elif isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)

    assert "app.core.projects.db" not in imported_modules
    assert "projects.db" not in src
