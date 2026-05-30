"""AST helpers for refactor-stable source guards.

These replace brittle ``assert "<name>" in src`` substring matching used by
many contract tests. Substring matching has two failure modes:
  - false-fail: the literal appears in a comment/docstring/string;
  - false-pass: the code is reformatted (e.g. ``except (ImportError, ...)``
    no longer contains the exact substring ``except ImportError``).

Working on the parsed AST removes both: identifiers are matched only where they
are *used as code*, and structural checks (handlers, imports, class defs) are
formatting-independent. See test_import_hygiene_residual_contract for usage.
"""

from __future__ import annotations

import ast


def all_identifiers(source: str) -> set[str]:
    """Every identifier *used as code* anywhere in ``source``.

    Covers Name references, attribute names (``self.x`` -> ``x``), positional
    and keyword argument names (``f(x=...)`` -> ``x``), function/class names,
    and import-bound names. Identifiers inside comments or string literals are
    NOT included — which is the whole point versus substring matching.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
                names.add(alias.name.split(".")[0])
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


def self_attribute_names(source: str) -> set[str]:
    """Names of attributes accessed on ``self`` (``self.x`` -> ``x``).

    Faithful replacement for ``assert "self.x" not in src`` instance-attribute
    guards, without false hits on an unrelated ``x`` identifier elsewhere.
    """
    return {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }


def function_param_names(source: str) -> set[str]:
    """Every parameter name across all function defs in ``source``."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
                names.add(arg.arg)
            if a.vararg:
                names.add(a.vararg.arg)
            if a.kwarg:
                names.add(a.kwarg.arg)
    return names


def module_classdefs(source: str) -> set[str]:
    """Names of every ``class`` defined anywhere in ``source``."""
    return {
        n.name
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.ClassDef)
    }


def function_def_names(source: str) -> set[str]:
    """Names of every ``def``/``async def`` in ``source`` (faithful for the
    ``"def foo" not in src`` family — matches a definition, not a call)."""
    return {
        n.name
        for n in ast.walk(ast.parse(source))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def imports_module(source: str, dotted: str) -> bool:
    """True if ``source`` imports ``dotted`` (``import dotted`` or
    ``from dotted import ...`` / ``from dotted.sub import ...``)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(a.name == dotted or a.name.startswith(dotted + ".") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == dotted or node.module.startswith(dotted + "."):
                return True
    return False


def imported_names(source: str) -> set[str]:
    """Every name appearing in an import statement: module dotted paths, their
    root packages, and imported/aliased symbol names. Faithful, robust
    replacement for iterating ``import``/``from`` lines and substring-matching.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
                names.add(alias.name.split(".")[0])
                if alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
                names.add(node.module.split(".")[0])
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
    return names


def has_fstring(source: str) -> bool:
    """True if ``source`` contains any f-string (security guard for SQL built
    by interpolation — robust vs. an ``f"`` substring inside a comment)."""
    return any(isinstance(n, ast.JoinedStr) for n in ast.walk(ast.parse(source)))


def calls_method(source: str, method: str) -> bool:
    """True if ``source`` calls ``<anything>.<method>(...)``."""
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == method
        for n in ast.walk(ast.parse(source))
    )


def uses_attribute(source: str, obj: str, attr: str) -> bool:
    """True if ``source`` accesses ``<obj>.<attr>`` (e.g. ``csv.writer``)."""
    return any(
        isinstance(n, ast.Attribute)
        and n.attr == attr
        and isinstance(n.value, ast.Name)
        and n.value.id == obj
        for n in ast.walk(ast.parse(source))
    )


def handler_catches(source: str, exc_name: str) -> bool:
    """True if any ``except`` handler catches ``exc_name`` (bare or in a tuple)."""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        caught = node.type
        candidates = caught.elts if isinstance(caught, ast.Tuple) else [caught]
        if any(isinstance(c, ast.Name) and c.id == exc_name for c in candidates):
            return True
    return False


def imports_symbol_from(source: str, module: str, symbol: str) -> bool:
    """True if ``source`` has ``from <module> import ... <symbol> ...``."""
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == module
            and any(alias.name == symbol for alias in node.names)
        ):
            return True
    return False


def call_has_keyword(source: str, func_name: str, keyword: str) -> bool:
    """True if ``source`` calls ``func_name(...)`` with a ``keyword=`` argument.

    ``func_name`` matches the called name whether invoked bare (``f(...)``) or
    as an attribute (``mod.f(...)``). Formatting-independent, unlike matching
    ``"f(\\n    keyword=" `` as a substring.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = (
            callee.id if isinstance(callee, ast.Name)
            else callee.attr if isinstance(callee, ast.Attribute)
            else None
        )
        if name == func_name and any(kw.arg == keyword for kw in node.keywords):
            return True
    return False
