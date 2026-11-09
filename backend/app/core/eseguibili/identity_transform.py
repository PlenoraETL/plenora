"""Trasformazione dell'input «Utente che esegue» (lato ``app.core``).

La regola e' in ``app/shared/execution/runtime_identity`` (sezione
TRASFORMAZIONE); qui c'e' solo il collegamento al motore della Formula di
Manipola, che ``app.shared`` non puo' importare:

- :func:`transform_problem`: l'espressione e' accettabile? Stesso cancello AST
  della Formula, piu' i nomi: deve citare la variabile ``utente`` e nessun
  altro nome (non ci sono altre colonne). La usano la compilazione del
  manifest (quindi pubblicazione, Valida, esecuzione) e l'anteprima del
  pannello dell'input.
- :func:`identity_values`: ``resolve_identity_inputs`` con il valutatore del
  motore della Formula. L'UNICO ingresso del backend per i valori degli
  input di tipo server (risoluzione del canvas e impronta della stima).
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from typing import Any

from app.shared.contracts.payloads import StringMap
from app.core.manipola.transforms.formula import (
    evaluate_formula_on_row,
    formula_names,
    validate_formula,
)
from app.shared.execution.runtime_identity import (
    KIND_LABEL,
    TRANSFORM_MAX_LENGTH,
    TRANSFORM_VARIABLE,
    RuntimeIdentity,
    resolve_identity_inputs,
    transform_identity_value,
)


def transform_problem(expression: str | None) -> str | None:
    """Motivo per cui la trasformazione non e' accettabile, ``None`` se lo e'
    (anche se assente: la trasformazione e' facoltativa)."""
    text = str(expression or "").strip()
    if not text:
        return None
    if len(text) > TRANSFORM_MAX_LENGTH:
        return f"la trasformazione supera {TRANSFORM_MAX_LENGTH} caratteri"
    try:
        validate_formula(text)
    except ValueError as exc:
        return f"la trasformazione non e' una formula ammessa ({exc})"
    bounded = _bounded_problem(text)
    if bounded:
        return bounded
    names = formula_names(text)
    others = sorted(names - {TRANSFORM_VARIABLE})
    if others:
        return (
            f"la trasformazione usa nomi sconosciuti ({', '.join(others)}): "
            f"l'unica variabile e' «{TRANSFORM_VARIABLE}»"
        )
    if TRANSFORM_VARIABLE not in names:
        return (
            f"la trasformazione non usa «{TRANSFORM_VARIABLE}»: darebbe lo stesso "
            "valore a tutti gli utenti"
        )
    return None


# Limiti di COSTO della trasformazione (controllo 09/11/2026, D2): il
# cancello della Formula ammette potenze e ripetizioni, che anche su una sola
# riga di testo possono bloccare il processo. Per il nome utente bastano
# testi, slice, metodi di testo e concatenazione con «+»: niente altri
# operatori aritmetici e solo numeri e testi piccoli.
TRANSFORM_MAX_NUMBER = 1000
TRANSFORM_MAX_TEXT = 200


def _bounded_problem(text: str) -> str | None:
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return "la trasformazione non e' un'espressione valida"
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and not isinstance(node.op, ast.Add):
            return "la trasformazione puo' usare solo «+» per unire i testi"
        if isinstance(node, ast.UnaryOp) and not isinstance(node.op, ast.USub):
            return "la trasformazione usa un operatore non ammesso"
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and abs(value) > TRANSFORM_MAX_NUMBER:
                return f"la trasformazione usa un numero oltre {TRANSFORM_MAX_NUMBER}"
            if isinstance(value, str) and len(value) > TRANSFORM_MAX_TEXT:
                return f"la trasformazione usa un testo oltre {TRANSFORM_MAX_TEXT} caratteri"
    return None


def assert_valid_transform(expression: str | None, *, label: str) -> None:
    problem = transform_problem(expression)
    if problem:
        raise ValueError(f"Input «{label}» ({KIND_LABEL}): {problem}.")


def evaluate_identity_transform(expression: str, row: Mapping[str, str]) -> Any:
    """Valutatore di ``runtime_identity`` (motore della Formula, una riga).

    Ricontrolla l'espressione anche qui (O2): un manifest salvato prima di un
    limite nuovo non lo aggira."""
    assert_valid_transform(expression, label="Utente")
    return evaluate_formula_on_row(expression, row)


def identity_values(
    definitions: Iterable[Any],
    *,
    identity: RuntimeIdentity,
    client_values: Mapping[str, Any],
) -> StringMap:
    """Valori degli input di tipo server per questa esecuzione."""
    return resolve_identity_inputs(
        definitions, identity=identity, client_values=client_values,
        evaluate=evaluate_identity_transform,
    )


def preview_transform(expression: str | None, username: str) -> str:
    """Anteprima del pannello: valore per il nome utente di prova, stessa
    regola dell'esecuzione. Solleva ``ValueError`` (espressione non
    accettabile) o ``RuntimeIdentityError`` (valutazione)."""
    assert_valid_transform(expression, label="Utente")
    return transform_identity_value(
        label="Utente", username=username, expression=str(expression or "").strip() or None,
        evaluate=evaluate_identity_transform,
    )


__all__ = [
    "assert_valid_transform",
    "evaluate_identity_transform",
    "identity_values",
    "preview_transform",
    "transform_problem",
]
