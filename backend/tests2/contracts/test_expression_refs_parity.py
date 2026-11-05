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


# ── Fase 5, T5+T6, correzione dopo la conferma: UN solo tokenizzatore ───────
# Frontend (copia JS sul corpus), canvas_sync e plugin_executor riscrivono i
# riferimenti con app/shared/execution/expression_refs. Prima plugin_executor
# aveva una terza copia con ``\bnome\b`` che riscriveva anche i letterali.

def _executor_rewrite(expression: str, old: str, new: str) -> str:
    from app.core.manipola.plugin_executor import get_executor

    return get_executor()._rewrite_expression_refs(expression, [{"id": "c1", "name": old}], {"c1": new})


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["expression"] or "<vuota>")
def test_plugin_executor_uses_the_same_rule(case):
    for rw in case["rewrites"]:
        assert _executor_rewrite(case["expression"], rw["old"], rw["new"]) == rw["expected"]


# ── Fase 5, T5+T6, seconda correzione: rinomina SIMULTANEA ovunque ─────────
# REGOLA: tutte le rinomine di un'espressione in UN passaggio
# (rewrite_expression_refs). I tre percorsi (plugin_executor, canvas_sync e il
# frontend rewriteExpressionRefs, nel test JS) danno il risultato del corpus
# su ogni caso, scambi e catene compresi.

def _refs_and_columns(renames: dict[str, str]):
    refs = [{"id": f"c{i}", "name": old} for i, old in enumerate(renames)]
    columns = [{"id": f"c{i}", "name": new} for i, new in enumerate(renames.values())]
    return refs, columns


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["expression"] or "<vuota>")
def test_simultaneous_rename_same_on_backend_paths(case):
    from app.core.manipola.plugin_executor import get_executor

    sim = case["simultaneous"]
    refs, columns = _refs_and_columns(sim["renames"])
    id_to_name = {c["id"]: c["name"] for c in columns}
    assert get_executor()._rewrite_expression_refs(case["expression"], refs, id_to_name) == sim["expected"]
    config = {"formula": case["expression"], "formula_column_refs": refs}
    out = canvas_sync._sync_expression_config(config, columns)
    assert out.get("formula", case["expression"]) == sim["expected"]


def test_corpus_has_swaps_and_chains():
    def kind(renames):
        olds, news = set(renames), set(renames.values())
        if any(renames.get(new) == old for old, new in renames.items()):
            return "swap"
        return "chain" if olds & news else "plain"
    kinds = {kind(c["simultaneous"]["renames"]) for c in CASES}
    assert {"swap", "chain"} <= kinds
    swap = next(c for c in CASES if c["expression"] == "a + b == 'a'")
    assert swap["simultaneous"]["expected"] == "b + a == 'a'"
    chain = next(c for c in CASES if c["expression"] == "a + b")
    assert chain["simultaneous"]["expected"] == "b + c"


def test_canvas_sync_migrations_rename_simultaneously():
    """Senza riferimenti salvati (progetti vecchi): rinomine dalle migrazioni."""
    migrations = {"name:a": {"id": "c1", "name": "b"}, "name:b": {"id": "c2", "name": "a"}}
    assert canvas_sync._name_map_from_migrations(migrations) == {"a": "b", "b": "a"}
    out = canvas_sync._sync_expression_config(
        {"formula": "a - b"}, [{"id": "c1", "name": "b"}, {"id": "c2", "name": "a"}], column_migrations=migrations,
    )
    assert out["formula"] == "b - a"


def test_plugin_executor_renames_simultaneously():
    from app.core.manipola.plugin_executor import get_executor

    out = get_executor()._rewrite_expression_refs(
        "a + b == 'a'", [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}], {"1": "b", "2": "a"},
    )
    assert out == "b + a == 'a'"


# Riscritture di nomi con espressioni regolari (``\b{re.escape(nome)}\b``,
# ``.replace(f"`{nome}`"...)``) fuori dal tokenizzatore: vietate. Eccezione
# dichiarata: il traduttore SQL rinomina colonne in testo SQL (altro
# linguaggio, non le espressioni delle formule).
_REGEX_REWRITE = (r"\b{re.escape(", "\\b{re.escape(", 'replace(f"`{', "replace(f'`{")
_ALLOWED = {"app/db/sql_translator.py": "testo SQL, non espressioni delle formule"}


def test_no_other_regex_rewrite_of_column_refs():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in (root / "app").rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel in _ALLOWED or rel == "app/shared/execution/expression_refs.py":
            continue
        text = path.read_text(encoding="utf-8")
        if any(pattern in text for pattern in _REGEX_REWRITE):
            offenders.append(rel)
    assert offenders == []
    front = root.parent / "frontend" / "src"
    if front.is_dir():
        js_offenders = [p.relative_to(front).as_posix() for p in front.rglob("*.js*")
                        if "\\b${" in p.read_text(encoding="utf-8") and p.name != "QueryPreview.jsx"
                        and p.name != "expressionRefs.js"]
        assert js_offenders == []
