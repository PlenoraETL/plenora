"""
Guardrail per mantenere il codice leggibile dopo la pulizia dei commenti.

La policy non pretende una migrazione big-bang: blocca la crescita del debito
su type hints e nomi deboli, cosi' ogni refactor puo' abbassare la baseline.
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

MAX_MISSING_ARG_ANNOTATIONS = 314
MAX_MISSING_RETURN_ANNOTATIONS = 348
MAX_WEAK_NAME_REFERENCES = 272

WEAK_NAMES = {
    "df2",
    "df3",
    "obj",
    "res",
    "ret",
    "tmp",
    "tmp2",
    "val",
    "x",
    "y",
    "z",
}


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


def _format_annotation_offenders(
    offenders: Sequence[Tuple[str, Tuple[int, int]]],
) -> str:
    """Formatta gli offender con colonne stabili e leggibili.

    @utils
    """
    if not offenders:
        return "  nessun offender"
    return "\n".join(
        f"  {path}: args={missing_args}, returns={missing_returns}"
        for path, (missing_args, missing_returns) in offenders
    )


def _format_count_offenders(offenders: Sequence[Tuple[str, int]]) -> str:
    """Formatta una classifica file -> conteggio.

    @utils
    """
    if not offenders:
        return "  nessun offender"
    return "\n".join(f"  {path}: {count}" for path, count in offenders)


def _function_annotation_debt(path: Path) -> Tuple[int, int]:
    """Conta parametri e return type mancanti in funzioni/metodi.

    @logic
    """
    missing_args = 0
    missing_returns = 0
    for node in ast.walk(_parse(path)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        if node.returns is None:
            missing_returns += 1

        args = (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )
        if node.args.vararg:
            args.append(node.args.vararg)
        if node.args.kwarg:
            args.append(node.args.kwarg)

        for arg in args:
            if arg.arg in {"self", "cls"}:
                continue
            if arg.annotation is None:
                missing_args += 1

    return missing_args, missing_returns


def _weak_name_count(path: Path) -> int:
    """Conta riferimenti a nomi troppo generici nei moduli runtime.

    @logic
    """
    count = 0
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Name) and node.id in WEAK_NAMES:
            count += 1
        elif isinstance(node, ast.arg) and node.arg in WEAK_NAMES:
            count += 1
    return count


def _scan_annotation_debt() -> Tuple[int, int, Dict[str, Tuple[int, int]]]:
    """Produce baseline globale e top offenders per type hints mancanti.

    @logic
    """
    total_missing_args = 0
    total_missing_returns = 0
    offenders: Dict[str, Tuple[int, int]] = {}
    for path in _iter_py_files():
        missing_args, missing_returns = _function_annotation_debt(path)
        total_missing_args += missing_args
        total_missing_returns += missing_returns
        if missing_args or missing_returns:
            offenders[_relative(path)] = (missing_args, missing_returns)
    return total_missing_args, total_missing_returns, offenders


def _scan_weak_names() -> Tuple[int, Dict[str, int]]:
    """Produce baseline globale e top offenders per nomi deboli.

    @logic
    """
    total = 0
    offenders: Dict[str, int] = {}
    for path in _iter_py_files():
        count = _weak_name_count(path)
        total += count
        if count:
            offenders[_relative(path)] = count
    return total, offenders


def test_runtime_type_hint_debt_does_not_increase() -> None:
    """Blocca la crescita del debito di type hints nel runtime backend.

    @logic
    """
    missing_args, missing_returns, offenders = _scan_annotation_debt()
    top_offenders = sorted(
        offenders.items(),
        key=lambda item: (-(item[1][0] + item[1][1]), item[0]),
    )[:20]

    assert missing_args <= MAX_MISSING_ARG_ANNOTATIONS, (
        "Parametri senza annotation oltre baseline.\n"
        f"Observed: {missing_args}; baseline: {MAX_MISSING_ARG_ANNOTATIONS}.\n"
        "La baseline puo' solo scendere dopo cleanup behavior-preserving.\n"
        "Top offenders:\n"
        f"{_format_annotation_offenders(top_offenders)}"
    )
    assert missing_returns <= MAX_MISSING_RETURN_ANNOTATIONS, (
        "Return type mancanti oltre baseline.\n"
        f"Observed: {missing_returns}; baseline: {MAX_MISSING_RETURN_ANNOTATIONS}.\n"
        "La baseline puo' solo scendere dopo cleanup behavior-preserving.\n"
        "Top offenders:\n"
        f"{_format_annotation_offenders(top_offenders)}"
    )


def test_weak_runtime_names_do_not_increase() -> None:
    """Blocca la crescita dei nomi deboli nei moduli runtime.

    @logic
    """
    total, offenders = _scan_weak_names()
    top_offenders = sorted(offenders.items(), key=lambda item: (-item[1], item[0]))[:20]

    assert total <= MAX_WEAK_NAME_REFERENCES, (
        "Nomi deboli oltre baseline.\n"
        f"Observed: {total}; baseline: {MAX_WEAK_NAME_REFERENCES}.\n"
        f"Nomi monitorati: {sorted(WEAK_NAMES)}\n"
        "Top offenders:\n"
        f"{_format_count_offenders(top_offenders)}"
    )
