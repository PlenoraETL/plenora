"""Riferimenti a colonna dentro un'espressione (Formula) e loro riscrittura.

REGOLA (Fase 5, T6, R3.12)
--------------------------
La riscrittura di un nome di colonna dentro un'espressione agisce SOLO sui
token che il motore delle formule legge come riferimenti a colonna:

- un nome (identificatore Python) che non e' una parola chiave, non e' un
  attributo (``x.str``: ``str``), non e' il nome di una funzione chiamata
  (``round(x)``: ``round``) e non e' il nome di un argomento
  (``round(x, ndigits=2)``: ``ndigits``);
- un nome tra backtick (forma di ``pandas.eval``: ```nome con spazi```).

Mai dentro le stringhe tra apici o virgolette (anche con prefisso ``r``/``b``/
``u``/``f`` e triple), mai nei numeri (``1e5``), mai nei commenti.

E' lo stesso riconoscimento di ``ast`` sulle espressioni valide
(``_validate_formula_ast`` di ``manipola/transforms/formula.py``: ``ast.Name``
fuori dalla posizione di funzione chiamata), esteso alle espressioni che non
si compilano ancora (scritte a meta'): un test confronta il tokenizzatore con
``ast`` su tutto il corpus ``tests2/contracts/expression_refs_cases.json``. La
copia del frontend (``frontend/src/utils/expressionRefs.js``) gira sullo
stesso corpus.
"""

from __future__ import annotations

import keyword
import re
from typing import NamedTuple

_NAME_RE = re.compile(r"[^\W\d]\w*")
_NUMBER_RE = re.compile(
    r"0[xXoObB][0-9a-fA-F_]+|(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9][0-9_]*)(?:[eE][+-]?[0-9][0-9_]*)?[jJ]?"
)
_STRING_PREFIXES = frozenset({"r", "u", "b", "f", "br", "rb", "fr", "rf"})
_KEYWORDS = frozenset(keyword.kwlist)
_DIGITS = "0123456789"


class ExpressionToken(NamedTuple):
    kind: str  # name | backtick | string | number | function | attribute | kwarg | keyword | comment | other
    start: int
    end: int
    text: str

    @property
    def ref(self) -> str | None:
        """Nome della colonna se il token e' un riferimento, altrimenti None."""
        if self.kind == "name":
            return self.text
        if self.kind == "backtick":
            return self.text[1:-1]
        return None


def _skip_spaces(expression: str, index: int, step: int) -> int:
    while 0 <= index < len(expression) and expression[index] in " \t\r\n\f\v":
        index += step
    return index


def _string_end(expression: str, index: int) -> int:
    """Fine della stringa che inizia con l'apice in ``index`` (esclusa)."""
    quote = expression[index]
    if expression.startswith(quote * 3, index):
        close = expression.find(quote * 3, index + 3)
        return len(expression) if close < 0 else close + 3
    i = index + 1
    while i < len(expression):
        char = expression[i]
        if char == "\\":
            i += 2
            continue
        if char == quote or char == "\n":
            return i + 1
        i += 1
    return len(expression)


def expression_tokens(expression: str) -> list[ExpressionToken]:
    """Token dell'espressione con il loro tipo (vedi la regola del modulo)."""
    tokens: list[ExpressionToken] = []
    if not isinstance(expression, str):
        return tokens
    i = 0
    n = len(expression)
    while i < n:
        char = expression[i]
        if char in " \t\r\n\f\v":
            i += 1
            continue
        if char == "#":
            end = expression.find("\n", i)
            end = n if end < 0 else end
            tokens.append(ExpressionToken("comment", i, end, expression[i:end]))
            i = end
            continue
        if char in "'\"":
            end = _string_end(expression, i)
            tokens.append(ExpressionToken("string", i, end, expression[i:end]))
            i = end
            continue
        if char == "`":
            close = expression.find("`", i + 1)
            if close < 0:
                tokens.append(ExpressionToken("other", i, n, expression[i:]))
                break
            tokens.append(ExpressionToken("backtick", i, close + 1, expression[i:close + 1]))
            i = close + 1
            continue
        if char in _DIGITS or (char == "." and i + 1 < n and expression[i + 1] in _DIGITS):
            match = _NUMBER_RE.match(expression, i)
            end = match.end() if match and match.end() > i else i + 1
            tokens.append(ExpressionToken("number", i, end, expression[i:end]))
            i = end
            continue
        match = _NAME_RE.match(expression, i)
        if match:
            text = match.group(0)
            end = match.end()
            if end < n and expression[end] in "'\"" and text.lower() in _STRING_PREFIXES:
                close = _string_end(expression, end)
                tokens.append(ExpressionToken("string", i, close, expression[i:close]))
                i = close
                continue
            tokens.append(ExpressionToken(_name_kind(expression, i, end, text), i, end, text))
            i = end
            continue
        tokens.append(ExpressionToken("other", i, i + 1, char))
        i += 1
    return tokens


def _name_kind(expression: str, start: int, end: int, text: str) -> str:
    if text in _KEYWORDS:
        return "keyword"
    before = _skip_spaces(expression, start - 1, -1)
    if before >= 0 and expression[before] == ".":
        return "attribute"
    after = _skip_spaces(expression, end, 1)
    if after < len(expression):
        if expression[after] == "(":
            return "function"
        if expression[after] == "=" and not expression.startswith("==", after):
            return "kwarg"
    return "name"


def expression_column_refs(expression: str) -> list[str]:
    """Nomi delle colonne citate, nell'ordine, senza doppioni."""
    return list(dict.fromkeys(t.ref for t in expression_tokens(expression) if t.ref is not None))


def expression_has_ref(expression: str, name: str) -> bool:
    if not expression or not name:
        return False
    return any(t.ref == name for t in expression_tokens(expression))


def _render_ref(token: ExpressionToken, new_name: str) -> str:
    return f"`{new_name}`" if token.kind == "backtick" else new_name


def rewrite_expression_ref(expression: str, old_name: str, new_name: str) -> str:
    """Sostituisce ``old_name`` con ``new_name`` solo nei riferimenti a colonna."""
    if not expression or not old_name or not new_name or old_name == new_name:
        return expression
    out: list[str] = []
    cursor = 0
    for token in expression_tokens(expression):
        if token.ref != old_name:
            continue
        out.append(expression[cursor:token.start])
        out.append(_render_ref(token, new_name))
        cursor = token.end
    if not out:
        return expression
    out.append(expression[cursor:])
    return "".join(out)


__all__ = [
    "ExpressionToken",
    "expression_column_refs",
    "expression_has_ref",
    "expression_tokens",
    "rewrite_expression_ref",
]
