"""Guards for domain exception ownership."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, Set, Tuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"
_PRODUCTION_ROOTS = (
    _APP_ROOT,
    _BACKEND_ROOT / "workers",
)

_CANONICAL_EXCEPTION_MODULES = {
    "app/core/arricchisci/exceptions.py",
    "app/core/files/exceptions.py",
    "app/core/projects/exceptions.py",
    "app/core/auth/exceptions.py",
    "app/core/intermediate/exceptions.py",
    "app/core/resources/exceptions.py",
}

_DOMAIN_EXCEPTION_NAMES = {
    "AuthAccessDeniedError",
    "AuthDomainError",
    "AuthResourceNotFoundError",
    "CanvasCorruptedError",
    "ExecutionContextError",
    "FileSourceResolutionError",
    "IntermediateSettingsError",
    "IntermediateWriteError",
    "MemoryLimitExceededError",
    "ProjectCleanupPartialError",
    "ProjectHasDependenciesError",
    "ProjectLineageCycleError",
}


def _rel(path: Path) -> str:
    return str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")


def _iter_py(root: Path) -> Iterable[Path]:
    yield from root.rglob("*.py")


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_defs(path: Path) -> Set[str]:
    return {
        node.name
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.ClassDef)
    }


def _db_modules_for_domains() -> Iterable[Path]:
    for domain in (
        "arricchisci",
        "auth",
        "files",
        "intermediate",
        "projects",
        "resources",
    ):
        domain_root = _APP_ROOT / "core" / domain
        for path in _iter_py(domain_root):
            if path.name == "exceptions.py":
                continue
            if path.name == "db.py" or path.name.endswith("_db.py"):
                yield path


def _production_modules() -> Iterable[Path]:
    for root in _PRODUCTION_ROOTS:
        if not root.exists():
            continue
        for path in _iter_py(root):
            yield path


def _shared_exception_imports(path: Path) -> Set[str]:
    hits: Set[str] = set()
    module_aliases: Set[str] = set()
    full_module_aliases: Set[Tuple[str, ...]] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name != "app.shared.exceptions":
                    continue
                hits.add(alias.asname or alias.name)
                if alias.asname:
                    module_aliases.add(alias.asname)
                else:
                    full_module_aliases.add(("app", "shared", "exceptions"))
            continue

        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module == "app.shared":
            for alias in node.names:
                if alias.name == "exceptions":
                    hits.add(alias.asname or alias.name)
                    module_aliases.add(alias.asname or alias.name)
            continue

        if node.module != "app.shared.exceptions":
            continue
        for alias in node.names:
            hits.add(alias.name)

    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr not in _DOMAIN_EXCEPTION_NAMES:
            continue
        chain = _attribute_chain(node.value)
        if chain in full_module_aliases:
            hits.add(node.attr)
            continue
        if len(chain) == 1 and chain[0] in module_aliases:
            hits.add(node.attr)
    return hits


def _attribute_chain(node: ast.AST) -> Tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_attribute_chain(node.value), node.attr)
    return ()


def test_domain_exception_modules_exist():
    missing = [
        rel
        for rel in sorted(_CANONICAL_EXCEPTION_MODULES)
        if not (_BACKEND_ROOT / rel).exists()
    ]
    assert missing == []


def test_domain_exceptions_not_defined_in_shared_exceptions():
    shared = _APP_ROOT / "shared" / "exceptions.py"
    if not shared.exists():
        return
    hits = _class_defs(shared) & _DOMAIN_EXCEPTION_NAMES
    assert hits == set(), (
        "Domain exceptions must live in their `core.<domain>.exceptions` "
        f"module, not shared: {sorted(hits)}"
    )


def test_domain_exceptions_not_defined_in_db_modules():
    hits: Set[Tuple[str, str]] = set()
    for path in _db_modules_for_domains():
        for name in _class_defs(path) & _DOMAIN_EXCEPTION_NAMES:
            hits.add((_rel(path), name))

    assert hits == set(), (
        "DB modules must import domain exceptions from "
        f"`core.<domain>.exceptions` instead of defining them ad hoc: "
        f"{sorted(hits)}"
    )


def test_production_imports_no_shared_exceptions_module():
    """Runtime code must import exceptions from the owning domain modules."""
    hits: Set[Tuple[str, str]] = set()
    for path in _production_modules():
        for name in _shared_exception_imports(path):
            hits.add((_rel(path), name))

    assert hits == set(), (
        "Production code must not import `app.shared.exceptions`; use "
        "`app.core.<domain>.exceptions` instead: "
        f"{sorted(hits)}"
    )


def test_shared_exceptions_tombstone_deleted():
    assert not (_APP_ROOT / "shared" / "exceptions.py").exists()
