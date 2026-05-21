"""
Contract test per la redazione dei secret nei logger.error OAuth2.

Bug coperto (plenora-bug-connections P1):
  Pre-fix `_fetch_oauth2_password_token` e
  `_fetch_oauth2_client_credentials_token` loggavano l'eccezione con
  `f"...: {e}"` su `logger.error`. Alcune eccezioni `requests.*` possono
  contenere stringhe del payload form-urlencoded (password,
  client_secret) nel `str(e)`. Il `SanitizingFilter` di Plenora copre
  i pattern noti ma non garantisce match su tutte le shape di
  RequestException; defense-in-depth richiede di non loggare `{e}` a
  monte.

Post-fix: i logger.error usano `type(e).__name__` come dettaglio
inline; il `exc_info=True` cattura il traceback completo, filtrato
dal `SanitizingFilter` sul `record.exc_text`.

Test AST-based: blinda l'invariante "nessun `f...{e}` o `%s % e` su
logger.error in oauth.py per le due funzioni vulnerabili".
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.shared.connections import oauth as oauth_module


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _logger_error_calls(fn: ast.FunctionDef) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "error":
            # Heuristic: chiamate `logger.error(...)` o `_logger.error(...)`.
            base = func.value
            base_name = getattr(base, "id", None) or getattr(base, "attr", None)
            if base_name and "logger" in str(base_name).lower():
                calls.append(node)
    return calls


def _arg_references_e_var(arg: ast.expr) -> bool:
    """True se l'argomento referenzia direttamente il nome `e` (la
    variabile dell'except clause).
    """
    for node in ast.walk(arg):
        if isinstance(node, ast.Name) and node.id == "e":
            # Se il riferimento e' wrappato in `type(e).__name__`, NON
            # e' un leak: ritorna False (e' il path safe).
            parent_call_safe = False
            # Scan ancestry tramite re-walk piu' attento sarebbe ideale,
            # ma per il pattern qui (un solo `type(e).__name__` o un nudo
            # `{e}`) basta un check stringy sull'ast.unparse(arg).
            src = ast.unparse(arg)
            if "type(e).__name__" in src:
                parent_call_safe = True
            if not parent_call_safe:
                return True
    return False


class TestOAuthLoggerErrorNoExceptionStr:
    """Verifica che `_fetch_oauth2_password_token` e
    `_fetch_oauth2_client_credentials_token` non logghino `{e}` o
    `% e` su logger.error nei loro except clause.
    """

    @pytest.mark.parametrize("fn_name", [
        "_fetch_oauth2_password_token",
        "_fetch_oauth2_client_credentials_token",
    ])
    def test_no_raw_exception_str_in_logger_error(self, fn_name):
        src_path = Path(oauth_module.__file__).resolve()
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        fn = _find_function(tree, fn_name)
        assert fn is not None, f"funzione {fn_name} non trovata"

        violations: list[str] = []
        for call in _logger_error_calls(fn):
            # Controlla tutti gli argomenti positionali della chiamata
            # logger.error.
            for arg in call.args:
                if _arg_references_e_var(arg):
                    violations.append(ast.unparse(call)[:200])

        assert not violations, (
            f"`{fn_name}`: trovate {len(violations)} chiamate "
            f"logger.error che includono `{{e}}` o `% e` raw senza "
            f"wrap di `type(e).__name__`. Leak potenziale di payload "
            f"OAuth2 (password / client_secret) nei log:\n  "
            + "\n  ".join(violations)
        )
