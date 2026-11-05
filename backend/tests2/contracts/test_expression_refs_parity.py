"""Riferimenti a colonna nelle espressioni (Fase 5, T6, R3.12).

1. Il corpus condiviso con il frontend e' aggiornato (stessi risultati di
   ``app.shared.execution.expression_refs``).
2. Sulle espressioni che si compilano, i riferimenti sono esattamente i
   ``ast.Name`` fuori dalla posizione di funzione chiamata (lo stesso parser
   del gate della Formula) e i letterali non cambiano mai con la riscrittura.
3. La sincronizzazione del canvas (``output_contract.canvas_sync``) usa la
   stessa regola: una rinomina a monte non tocca i letterali.
"""

from __future__ import annotations

import ast
import json

import pytest

from app.core.output_contract import canvas_sync
from app.shared.execution.expression_refs import expression_column_refs, rewrite_expression_ref
from tests2.contracts.generate_expression_refs_cases import CASES_PATH, CORPUS, build_cases

CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))


def test_corpus_file_is_current():
    assert CASES == build_cases(), "rigenerare con python -m tests2.contracts.generate_expression_refs_cases"
    assert len(CASES) == len(CORPUS)


def _ast_refs(expression: str) -> list[str] | None:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return None
    funcs = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    names = sorted(((n.lineno, n.col_offset), n.id) for n in ast.walk(tree)
                   if isinstance(n, ast.Name) and id(n) not in funcs)
    return list(dict.fromkeys(name for _, name in names))


def _literals(expression: str) -> list[object]:
    tree = ast.parse(expression, mode="eval")
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["expression"] or "<vuota>")
def test_tokenizer_matches_ast(case):
    expected = _ast_refs(case["expression"])
    if expected is None:
        pytest.skip("espressione non compilabile: solo il corpus")
    assert expression_column_refs(case["expression"]) == expected


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["expression"] or "<vuota>")
def test_rewrite_never_touches_literals(case):
    if _ast_refs(case["expression"]) is None:
        pytest.skip("espressione non compilabile")
    for rw in case["rewrites"]:
        rewritten = rewrite_expression_ref(case["expression"], rw["old"], rw["new"])
        if not rw["new"].isidentifier() and rw["old"] in expression_column_refs(case["expression"]):
            continue
        if "`" not in rewritten:
            assert _literals(rewritten) == _literals(case["expression"])


def test_audit_scenario():
    assert rewrite_expression_ref("stato == 'stato'", "stato", "status") == "status == 'stato'"


def test_canvas_sync_uses_the_same_rule():
    config = {"formula": "stato == 'stato'", "formula_column_refs": [{"id": "c1", "name": "stato"}]}
    columns = [{"id": "c1", "name": "status"}]
    out = canvas_sync._sync_expression_config(config, columns)
    assert out["formula"] == "status == 'stato'"
    assert canvas_sync._expression_has_token("x == 'y'", "y") is False
