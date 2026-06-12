"""
Guarda architetturale sugli import locali nel backend.

Gli import devono stare a livello modulo. Gli import dentro funzioni o metodi
restano ammessi solo come debito controllato per cicli, dipendenze opzionali o
bootstrap costoso.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, Iterable, Sequence, Tuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_SCAN_ROOTS = (
    _BACKEND_ROOT / "app",
    _BACKEND_ROOT / "workers",
)

# Baseline corrente: ogni nuovo import locale aumenta accoppiamento nascosto.
# La soglia puo' solo scendere; se sale, il reviewer deve giustificare il caso.
MAX_LOCAL_IMPORTS = 272
MAX_LATE_MODULE_IMPORTS = 8


def _iter_py_files() -> Iterable[Path]:
    """Restituisce i file Python di app e workers.

    @utils
    """
    for root in _SCAN_ROOTS:
        yield from sorted(root.rglob("*.py"))


def _relative(path: Path) -> str:
    """Normalizza un path relativo alla root backend.

    @utils
    """
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _parse(path: Path) -> ast.AST:
    """Parsa un file Python con encoding repo-standard.

    @utils
    """
    return ast.parse(path.read_text(encoding="utf-8"))


def _format_count_offenders(offenders: Sequence[Tuple[str, int]]) -> str:
    """Formatta una classifica file -> conteggio.

    @utils
    """
    if not offenders:
        return "  nessun offender"
    return "\n".join(f"  {path}: {count}" for path, count in offenders)


def _local_import_count(path: Path) -> int:
    """Conta import non top-level in un file Python.

    @logic
    """
    tree = _parse(path)
    parents: Dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if not isinstance(parents.get(node), ast.Module):
                count += 1
    return count


def _late_module_import_count(path: Path) -> int:
    """Conta import top-level dichiarati dopo codice eseguibile.

    @logic
    """
    tree = _parse(path)
    first_code_line = None
    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            continue
        first_code_line = statement.lineno
        break

    if first_code_line is None:
        return 0

    return sum(
        1
        for statement in tree.body
        if isinstance(statement, (ast.Import, ast.ImportFrom))
        and statement.lineno > first_code_line
    )


def _scan_local_imports() -> Dict[str, int]:
    """Produce la mappa file -> numero di import locali.

    @logic
    """
    counts: Dict[str, int] = {}
    for path in _iter_py_files():
        count = _local_import_count(path)
        if count:
            counts[_relative(path)] = count
    return counts


def _scan_late_module_imports() -> Dict[str, int]:
    """Produce la mappa file -> numero di import module-level tardivi.

    @logic
    """
    counts: Dict[str, int] = {}
    for path in _iter_py_files():
        count = _late_module_import_count(path)
        if count:
            counts[_relative(path)] = count
    return counts


def test_local_imports_do_not_increase() -> None:
    """Blocca la crescita degli import locali nel backend.

    @logic
    """
    counts = _scan_local_imports()
    total = sum(counts.values())
    top_offenders = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:20]

    assert total <= MAX_LOCAL_IMPORTS, (
        "Import locali oltre baseline.\n"
        f"Observed: {total}; baseline: {MAX_LOCAL_IMPORTS}.\n"
        "Regola: import top-level di default. Import locali solo per cicli, "
        "dipendenze opzionali o bootstrap costoso, con motivazione esplicita.\n"
        "Top offenders:\n"
        f"{_format_count_offenders(top_offenders)}"
    )


def test_late_module_imports_do_not_increase() -> None:
    """Blocca la crescita degli import top-level dopo codice eseguibile.

    @logic
    """
    counts = _scan_late_module_imports()
    total = sum(counts.values())
    top_offenders = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:20]

    assert total <= MAX_LATE_MODULE_IMPORTS, (
        "Import module-level tardivi oltre baseline.\n"
        f"Observed: {total}; baseline: {MAX_LATE_MODULE_IMPORTS}.\n"
        "Regola: import in cima al file. Le eccezioni devono essere boundary "
        "di bootstrap/wiring o moduli con ciclo reale.\n"
        "Top offenders:\n"
        f"{_format_count_offenders(top_offenders)}"
    )
