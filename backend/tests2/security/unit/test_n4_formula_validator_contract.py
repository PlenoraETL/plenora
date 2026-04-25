"""Contract test N4 — security invariants di op_formula.

Il nodo FORMULA di MANIPOLA esegue eval() su stringhe utente. Due strati
di protezione esistono nel codice:

1. AST validator (`_validate_formula_ast`): blocca gadget chain, import,
   class traversal, getattr/setattr, __builtins__ access, ecc.
2. Namespace ristretto: eval invocato con `__builtins__={}`.

Questo contract congela il comportamento attuale:

Sezione 1: payload di attacco classici -> ValueError (AST validator)
Sezione 2: formule lecite passano il validator
Sezione 3: nomi colonna "_"-prefix/dunder vengono scartati dal local_vars
           popolato in op_formula (defense-in-depth: se un attacker
           riuscisse a iniettare una colonna con nome speciale, non
           finisce nel namespace dell'eval)

NB: il codice prod non e' cambiato nei payload della sezione 1 — sono
gia' bloccati. I test esistono come rete anti-regressione: se qualcuno
allenta il validator domani, uno di questi test fallisce e si accorge.
"""

from __future__ import annotations

import logging
import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# =============================================================================
# 1. AST validator — payload di attacco bloccati
# =============================================================================


@pytest.mark.parametrize("payload", [
    # Gadget chain classica Python
    "col.__class__.__base__.__subclasses__()",
    "col.__class__.__base__",
    "col.__class__",
    "col.__bases__",
    "col.__mro__",
    "().__class__.__bases__[0].__subclasses__()",
    "[x for x in ().__class__.__bases__[0].__subclasses__() if x.__name__ == 'Popen']",
    # Builtin bypass
    "__import__('os').system('id')",
    "__import__",
    "__builtins__",
    # Attribute escape tramite getattr/setattr
    "getattr(col, '__class__')",
    "setattr(col, 'x', 1)",
    "delattr(col, 'x')",
    # Exec/eval annidati
    "eval('1+1')",
    "exec('pass')",
    "compile('1+1', '', 'eval')",
    # Introspection dangerosa
    "globals()",
    "locals()",
    "vars()",
    "dir(col)",
    "type(col)",
    "breakpoint()",
    # Apertura file/risorse
    "open('/etc/passwd')",
    # Attributi privati generici (regola _-prefix non whitelisted)
    "col._private_attr",
    "col.__hidden__",
    # Nodi AST proibiti
    "def f(): pass",
    "class X: pass",
])
def test_ast_validator_blocks_dangerous_payloads(payload):
    from app.core.manipola.transforms.base import _validate_formula_ast
    with pytest.raises(ValueError):
        _validate_formula_ast(payload)


@pytest.mark.parametrize("payload", [
    # Costrutti Python che oggi passano il blocklist, ma non servono a
    # formule pandas e sono vettori di attacco potenziali (comprehension
    # come entry point per gadget chain, walrus come side-effect,
    # f-string con embed, ecc.). Whitelist positiva li rifiuta tutti.
    "(x := 5)",                         # walrus / NamedExpr
    'f"{col}"',                         # f-string / JoinedStr
    "[x for x in col]",                 # list comprehension
    "{x for x in col}",                 # set comprehension
    "{x: x for x in col}",              # dict comprehension
    "(x for x in col)",                 # generator expression
    "lambda x: x + 1",                  # lambda
    "col if col > 0 else 0",            # conditional expression (IfExp)
    "col[0:5]",                         # slice/subscript
    "[1, 2, 3]",                        # list literal
    "(1, 2, 3)",                        # tuple literal
    "{1, 2, 3}",                        # set literal
    '{"a": 1}',                         # dict literal
])
def test_ast_validator_blocks_exotic_nodes_whitelist(payload):
    """Whitelist positiva: solo i nodi necessari a formule pandas sono
    ammessi. Comprehension/lambda/walrus/f-string/slice/literal containers
    non servono e ingrandiscono la superficie — rifiutati."""
    from app.core.manipola.transforms.base import _validate_formula_ast
    with pytest.raises(ValueError):
        _validate_formula_ast(payload)


# =============================================================================
# 2. AST validator — formule lecite passano
# =============================================================================


@pytest.mark.parametrize("formula", [
    "col + 1",
    "col * 2 - 3",
    "col1 + col2",
    "(col1 + col2) / col3",
    "col.str.upper()",
    "col.str.strip()",
    "col.str.contains('foo')",
    "col.fillna(0)",
    "col.astype(int)",
    "col.abs()",
    "col.round(2)",
    "col.isna()",
    "pd.to_numeric(col)",
    "col > 10",
    "(col > 0) & (col < 100)",
    # Operatori unari (whitelist deve coprirli)
    "-col",
    "+col",
    "~col",           # invert (negazione boolean su Series)
    "not (col > 10)",
    # Operatori binari meno comuni
    "col % 2",
    "col ** 2",
    "col // 2",
    # Boolean op
    "(col > 0) | (col < -10)",
    # Keyword args in chiamate (devono passare)
    "col.fillna(value=0)",
    "pd.to_numeric(col, errors='coerce')",
])
def test_ast_validator_allows_legitimate_formulas(formula):
    from app.core.manipola.transforms.base import _validate_formula_ast
    # Non deve sollevare
    _validate_formula_ast(formula)


# =============================================================================
# 3. op_formula — nomi colonna _-prefix/dunder scartati dal namespace eval
# =============================================================================


class _ListHandler(logging.Handler):
    """Handler custom: colleziona LogRecord in lista. Serve per aggirare
    il logger custom con propagate=False (caplog non arriva al target).
    """
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def base_log_records():
    """Attacca un ListHandler al logger di base.py e restituisce la lista."""
    target = logging.getLogger("app.core.manipola.transforms.base")
    handler = _ListHandler()
    target.addHandler(handler)
    try:
        yield handler.records
    finally:
        target.removeHandler(handler)


def test_op_formula_skips_dunder_column_from_eval_namespace():
    """Colonna chiamata '__import__' deve essere esclusa da local_vars
    e la formula che la referenzia deve essere rifiutata fail-loud
    (ValueError). Contratto attuale: op_formula rilancia errori AST/eval
    come ValueError invece di tornare il df originale silenziosamente."""
    from app.core.manipola.transforms.base import op_formula

    df = pd.DataFrame({
        "normal": [1, 2, 3],
        "__import__": [10, 20, 30],
    })
    with pytest.raises(ValueError):
        op_formula(df, "__import__ + 1", new_column="calc")


def test_op_formula_skips_single_underscore_prefix():
    """Colonne con prefix _ (semi-private) escluse per defense-in-depth.
    Una formula che le referenzia non trova il nome nel namespace eval
    e op_formula solleva ValueError (contratto fail-loud)."""
    from app.core.manipola.transforms.base import op_formula

    df = pd.DataFrame({
        "normal": [1, 2, 3],
        "_hidden": [100, 200, 300],
    })
    with pytest.raises(ValueError):
        op_formula(df, "_hidden + 1", new_column="calc")


def test_op_formula_accepts_normal_column_names():
    """Sanity: colonne con nomi normali funzionano."""
    from app.core.manipola.transforms.base import op_formula

    df = pd.DataFrame({"a": [1, 2, 3], "b": [10, 20, 30]})
    result = op_formula(df, "a + b", new_column="c")
    assert "c" in result.columns
    assert list(result["c"]) == [11, 22, 33]


def test_op_formula_skip_logs_warning(base_log_records):
    """Quando si scarta una colonna dunder, viene emesso un warning."""
    from app.core.manipola.transforms.base import op_formula

    df = pd.DataFrame({"normal": [1], "__dunder__": [2]})
    # Uso una formula che NON referenzia la colonna scartata, cosi'
    # il path di skip e' percorso ma la formula riesce comunque.
    op_formula(df, "normal + 1", new_column="c")
    skip_records = [
        r for r in base_log_records
        if r.levelno == logging.WARNING and "scart" in r.getMessage().lower()
    ]
    assert len(skip_records) >= 1
