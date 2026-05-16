"""Reject solitary ``pass`` stubs in runtime-heavy backend modules."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, List


# Path radice da scansionare (relative a backend/)
_DEFAULT_ROOTS = (
    "app/core/manipola/transforms",
    "app/core/estrai",
    "app/core/arricchisci",
    "app/core/carica",
    "app/core/scheduler",
    "app/db",
    "app/core/files",
)


# Nomi (file::funzione) deliberatamente esenti. Mantenere stretto:
# ogni nuova entry richiede commento spiegando perche'.
_EXEMPT_NAMES: set[str] = {
    # Compat layer: post pool psycopg2 refactor, close_connection e'
    # diventato un no-op (il pool gestisce il lifecycle). Mantengono la
    # firma per non rompere `DBConnectionCleanupMiddleware` che chiama
    # close_connection() dopo ogni request.
    "app/db/cleanup_middleware.py::_close_connections",
    "app/db/pg_connection.py::close_connection",
}


class _Violation:
    __slots__ = ("path", "lineno", "context")

    def __init__(self, path: Path, lineno: int, context: str) -> None:
        self.path = path
        self.lineno = lineno
        self.context = context

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}: pass solitario in {self.context}"


def _stmt_is_pass(node: ast.stmt) -> bool:
    return isinstance(node, ast.Pass)


def _body_is_only_pass(body: List[ast.stmt]) -> bool:
    """Return True when a body is effectively only ``pass``."""
    # Strip leading docstring
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return len(body) == 1 and _stmt_is_pass(body[0])


_NO_OP_CLASS_PATTERNS = ("Null", "NoOp", "Stub", "Mock", "Fake", "Dummy", "Default")


def _enclosing_class_is_noop(stack: List[ast.AST]) -> bool:
    """Vero se la funzione e' definita dentro una classe con nome
    Null/NoOp/Stub/Mock/Fake/Dummy/Default — questi sono pattern
    deliberati di "implementazione vuota di un Protocol" (no-op
    logger, time provider testabile, ecc.). I `pass` qui sono OK."""
    for ancestor in reversed(stack):
        if isinstance(ancestor, ast.ClassDef):
            for pat in _NO_OP_CLASS_PATTERNS:
                if pat in ancestor.name:
                    return True
            return False
    return False


def _check_file(path: Path, backend_root: Path) -> List[_Violation]:
    """AST-scan di un file. Ritorna lista di violazioni trovate.

    Il qualificatore di _EXEMPT_NAMES usa il path relativo a
    ``backend/`` cosi' che spostando la repo le exemption restino
    stabili.
    """
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []

    violations: List[_Violation] = []
    rel_path = path
    try:
        qual_prefix = path.relative_to(backend_root).as_posix()
    except ValueError:
        qual_prefix = path.as_posix()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._stack: List[ast.AST] = []

        def _ctx_label(self, node: ast.AST) -> str:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return f"funzione `{node.name}`"
            if isinstance(node, (ast.With, ast.AsyncWith)):
                items = ", ".join(
                    ast.unparse(item.context_expr).strip()[:40]
                    for item in node.items
                )
                return f"with {items}"
            if isinstance(node, ast.If):
                test = ast.unparse(node.test).strip()[:60]
                return f"branch if/elif/else (test={test})"
            return type(node).__name__

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            self._stack.append(node)
            self.generic_visit(node)
            self._stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            qual = f"{qual_prefix}::{node.name}"
            if (
                qual not in _EXEMPT_NAMES
                and not _enclosing_class_is_noop(self._stack)
                and not _is_protocol_or_abstract(node)
                and _body_is_only_pass(node.body)
            ):
                violations.append(_Violation(rel_path, node.lineno, self._ctx_label(node)))
            self._stack.append(node)
            self.generic_visit(node)
            self._stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            # `with ...: pass` significa "ho acquisito un lock per non
            # fare nulla", che e' esattamente il bug rate_limit_hits.
            if _body_is_only_pass(node.body):
                violations.append(_Violation(rel_path, node.lineno, self._ctx_label(node)))
            self.generic_visit(node)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            if _body_is_only_pass(node.body):
                violations.append(_Violation(rel_path, node.lineno, self._ctx_label(node)))
            self.generic_visit(node)

        def visit_If(self, node: ast.If) -> None:  # noqa: N802
            for branch in (node.body, node.orelse):
                if branch and _body_is_only_pass(branch):
                    # Prima riga della branch (non del test)
                    line = branch[0].lineno
                    violations.append(_Violation(rel_path, line, self._ctx_label(node)))
            self.generic_visit(node)

    Visitor().visit(tree)
    return violations


def _is_protocol_or_abstract(func: ast.FunctionDef) -> bool:
    """``pass`` is allowed for explicitly abstract or overload methods."""
    for dec in func.decorator_list:
        # @abstractmethod / @abc.abstractmethod / @overload
        name = ast.unparse(dec).strip()
        if name in {"abstractmethod", "abc.abstractmethod", "overload", "typing.overload"}:
            return True
    return False


def _walk_python_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        if root.suffix == ".py":
            yield root
        return
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def main(argv: List[str]) -> int:
    backend_root = Path(__file__).resolve().parent.parent
    if len(argv) > 1:
        roots = [Path(argv[1])]
    else:
        roots = [backend_root / r for r in _DEFAULT_ROOTS]

    all_violations: List[_Violation] = []
    files_scanned = 0
    for root in roots:
        if not root.exists():
            continue
        for path in _walk_python_files(root):
            files_scanned += 1
            all_violations.extend(_check_file(path, backend_root))

    if all_violations:
        print(f"[lint_pass_stubs] {len(all_violations)} violazioni trovate "
              f"in {files_scanned} file scansionati:\n")
        for v in all_violations:
            print(f"  {v}")
        print(
            "\nUn `pass` solitario in funzione/with/if richiede una scelta esplicita:\n"
            "  - implementare il branch mancante;\n"
            "  - sostituire con `raise NotImplementedError(...)` se intenzionale;\n"
            "  - se davvero no-op deliberato, aggiungere il qualifier\n"
            "    a `_EXEMPT_NAMES` in scripts/lint_pass_stubs.py con\n"
            "    un commento esplicativo (no pragma in-line)."
        )
        return 1

    print(f"[lint_pass_stubs] OK: 0 violazioni in {files_scanned} file.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
