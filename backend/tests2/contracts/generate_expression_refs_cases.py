"""Genera ``expression_refs_cases.json`` (Fase 5, T6, R3.12).

Corpus condiviso del riconoscimento dei riferimenti a colonna nelle
espressioni: i risultati attesi sono quelli di
``app.shared.execution.expression_refs`` (il backend). Il test
``test_expression_refs_parity.py`` verifica che il file sia aggiornato e che
il tokenizzatore coincida con ``ast`` sulle espressioni valide; il frontend
(``frontend/tests/shared/expressionRefs.contract.test.jsx``) fa girare la
copia JS sugli stessi casi.

Uso (da backend/):  python -m tests2.contracts.generate_expression_refs_cases
"""

from __future__ import annotations

import json
from pathlib import Path

from app.shared.execution.expression_refs import (
    expression_column_refs,
    rewrite_expression_ref,
    rewrite_expression_refs,
)

CASES_PATH = Path(__file__).with_name("expression_refs_cases.json")

# (espressione, [(vecchio nome, nuovo nome), ...])
CORPUS: list[tuple[str, list[tuple[str, str]]]] = [
    ("stato == 'stato'", [("stato", "status")]),
    ('stato == "stato"', [("stato", "status")]),
    ("'stato' + stato + \"stato\"", [("stato", "status")]),
    ("stato == 'l\\'stato'", [("stato", "status")]),
    ("stato == r'stato'", [("stato", "status")]),
    ("stato == b'stato'", [("stato", "status")]),
    ("stato == '''stato'''", [("stato", "status")]),
    ('stato == """stato \' stato"""', [("stato", "status")]),
    ("prezzo * quantita", [("prezzo", "costo"), ("quantita", "pezzi")]),
    ("prezzo*quantita+prezzo", [("prezzo", "costo")]),
    ("round(round)", [("round", "arrotonda")]),
    ("round(x, ndigits=2)", [("ndigits", "cifre"), ("x", "y")]),
    ("abs (x)", [("abs", "assoluto"), ("x", "y")]),
    ("nome.str.upper()", [("nome", "name"), ("str", "testo"), ("upper", "su")]),
    ("nome . str . lower ( )", [("nome", "name"), ("lower", "giu")]),
    ("x == 1e5", [("e5", "esp"), ("x", "y")]),
    ("x + 0x1F + 1_000 + 2.5j + .5", [("x", "y"), ("x1F", "z")]),
    ("x if y else z", [("x", "a"), ("y", "b")]),
    ("a and b or not c", [("a", "x"), ("c", "z")]),
    ("x is None", [("x", "y")]),
    ("True + flag", [("True", "Vero"), ("flag", "bandiera")]),
    ("`nome completo` + nome", [("nome completo", "full name"), ("nome", "name")]),
    ("`nome` * 2", [("nome", "name")]),
    ("città * 2", [("città", "comune")]),
    ("perché + città_2", [("perché", "motivo"), ("città_2", "c2")]),
    ("x  # commento con x", [("x", "y")]),
    ("x == 'non chiusa", [("x", "y")]),
    ("pd.to_numeric(valore)", [("valore", "importo"), ("pd", "pandas")]),
    ("valore.fillna(0).astype(int)", [("valore", "importo"), ("int", "intero")]),
    ("(a+b)*(a-b)", [("a", "x")]),
    ("aa + a + a_a", [("a", "x")]),
    ("-x ** 2", [("x", "y")]),
    ("x != x", [("x", "y")]),
    ("x==y", [("x", "y")]),
    ("f(x)(y)", [("x", "a"), ("y", "b")]),
    ("x + `", [("x", "y")]),
    ("", [("x", "y")]),
    ("'solo testo'", [("solo", "y")]),
    ("nome + ' ' + cognome", [("nome", "n"), ("cognome", "c")]),
    ("concat_ws('-', a, b)", [("concat_ws", "c"), ("a", "x")]),
    ("x\n+ y", [("y", "z")]),
    # Fase 5, T5+T6, seconda correzione: rinomina SIMULTANEA. Scambi e
    # catene: ogni riferimento si legge una volta sull'espressione di
    # partenza (in sequenza a<->b darebbe "a + a", a->b,b->c "c + c").
    ("a + b == 'a'", [("a", "b"), ("b", "a")]),
    ("a * b - `a`", [("a", "b"), ("b", "a")]),
    ("a + b", [("a", "b"), ("b", "c")]),
    ("b + a", [("b", "c"), ("a", "b")]),
    ("x + y + z", [("x", "y"), ("y", "z"), ("z", "x")]),
    ("round(a, ndigits=b) + 'b'", [("a", "b"), ("b", "a")]),
]


def build_cases() -> list[dict]:
    cases = []
    for expression, renames in CORPUS:
        cases.append({
            "expression": expression,
            "refs": expression_column_refs(expression),
            "rewrites": [
                {"old": old, "new": new, "expected": rewrite_expression_ref(expression, old, new)}
                for old, new in renames
            ],
            # Tutte le rinomine del caso insieme (un solo passaggio).
            "simultaneous": {
                "renames": dict(renames),
                "expected": rewrite_expression_refs(expression, dict(renames)),
            },
        })
    return cases


def main() -> None:
    CASES_PATH.write_text(json.dumps(build_cases(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
