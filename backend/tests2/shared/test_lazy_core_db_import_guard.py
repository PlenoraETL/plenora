"""Ratchet intra-function lazy imports of ``app.core.<domain>.db``.

Lazy DB imports are tolerated only when they are explicitly ledgered with a
removal path. New domain/router lazy imports should move behind a service or
runtime boundary instead of extending this list.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.architecture]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"

_ALLOWED_SEVERITIES = frozenset({"HIGH", "MEDIUM", "LOW"})
_ALLOWED_REASON_PREFIXES = (
    "BOOTSTRAP_BOUNDARY:",
    "RUNTIME_BOUNDARY:",
    "BLOCKER_WITH_REMOVAL_CONDITION:",
)


@dataclass(frozen=True)
class LazyCoreDbImport:
    src_path: str
    function_name: str
    imported_module: str
    imported_name: str
    line: int


@dataclass(frozen=True)
class AllowedLazyCoreDbImport:
    src_path: str
    function_name: str
    imported_module: str
    imported_name: str
    severity: str
    reason: str


_ALLOWED_LAZY_CORE_DB_IMPORTS: tuple[AllowedLazyCoreDbImport, ...] = (
)


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _enclosing_function_name(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> str | None:
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
        parent = parents.get(parent)
    return None


def _lazy_core_db_imports_from(path: Path) -> list[LazyCoreDbImport]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    imports: list[LazyCoreDbImport] = []
    for node in ast.walk(tree):
        function_name = _enclosing_function_name(node, parents)
        if function_name is None:
            continue

        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("app.core.") and node.module.endswith(".db"):
                for alias in node.names:
                    imports.append(
                        LazyCoreDbImport(
                            src_path=_rel(path),
                            function_name=function_name,
                            imported_module=node.module,
                            imported_name=alias.name,
                            line=node.lineno,
                        )
                    )
            elif node.module.startswith("app.core."):
                for alias in node.names:
                    if alias.name == "db":
                        imports.append(
                            LazyCoreDbImport(
                                src_path=_rel(path),
                                function_name=function_name,
                                imported_module=node.module,
                                imported_name=alias.name,
                                line=node.lineno,
                            )
                        )

        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app.core.") and alias.name.endswith(".db"):
                    imports.append(
                        LazyCoreDbImport(
                            src_path=_rel(path),
                            function_name=function_name,
                            imported_module=alias.name,
                            imported_name="<module>",
                            line=node.lineno,
                        )
                    )

    return imports


def _current_lazy_core_db_imports() -> list[LazyCoreDbImport]:
    imports: list[LazyCoreDbImport] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        imports.extend(_lazy_core_db_imports_from(path))
    return sorted(
        imports,
        key=lambda item: (
            item.src_path,
            item.function_name,
            item.imported_module,
            item.imported_name,
            item.line,
        ),
    )


def _edge_key(
    edge: LazyCoreDbImport | AllowedLazyCoreDbImport,
) -> tuple[str, str, str, str]:
    return (
        edge.src_path,
        edge.function_name,
        edge.imported_module,
        edge.imported_name,
    )


def test_lazy_core_db_imports_do_not_grow_without_rationale() -> None:
    allowed_by_key = {
        _edge_key(edge): edge for edge in _ALLOWED_LAZY_CORE_DB_IMPORTS
    }
    current_imports = _current_lazy_core_db_imports()
    violations = [
        edge for edge in current_imports if _edge_key(edge) not in allowed_by_key
    ]

    assert not violations, (
        "Intra-function lazy imports of app.core.<domain>.db must not grow. "
        "Move the dependency behind a service/runtime boundary, or add a "
        "narrow allowlist entry with severity and removal condition. "
        f"Violations: {violations}"
    )

    current_keys = {_edge_key(edge) for edge in current_imports}
    stale_allowed = [
        edge for edge in _ALLOWED_LAZY_CORE_DB_IMPORTS if _edge_key(edge) not in current_keys
    ]
    assert not stale_allowed, (
        "Lazy DB import allowlist drift detected. Remove stale entries: "
        f"{stale_allowed}"
    )


def test_lazy_core_db_import_allowlist_has_rationales() -> None:
    keys = [_edge_key(edge) for edge in _ALLOWED_LAZY_CORE_DB_IMPORTS]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates

    for edge in _ALLOWED_LAZY_CORE_DB_IMPORTS:
        assert edge.severity in _ALLOWED_SEVERITIES, edge
        assert edge.reason.startswith(_ALLOWED_REASON_PREFIXES), (
            f"{edge.src_path}:{edge.function_name} must explain why the lazy "
            "DB import remains and how it will be removed."
        )
        assert "remove" in edge.reason.lower(), (
            f"{edge.src_path}:{edge.function_name} rationale must include a "
            "removal path."
        )


def test_lazy_core_db_import_baseline_count_is_explicit() -> None:
    assert len(_current_lazy_core_db_imports()) == len(_ALLOWED_LAZY_CORE_DB_IMPORTS) == 0
