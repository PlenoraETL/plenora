"""
Architectural lint: invarianti TLS safety cross-codebase.

Blinda due regole anti-MITM su tutto il sorgente `backend/app/`:

1. NESSUN modulo deve chiamare `urllib3.disable_warnings(...)` (o
   `disable_warnings(...)` plain) a livello module-level. Quella chiamata
   sopprime gli InsecureRequestWarning per l'INTERO processo Python,
   mascherando future regressioni `verify=False` introdotte da terzi.
   Se serve sopprimere il warning per uno scope ristretto, usare
   `warnings.catch_warnings()` + `simplefilter("ignore", ...)`.

2. NESSUNA chiamata HTTP outbound deve usare `verify=False` come
   costante literal. La policy MITM-prevention richiede di passare il
   flag attraverso `app.shared.security.tls_policy.resolve_tls_verify`,
   che applica la regola env-aware + opt-in. Solo cosi' il bypass
   diventa rifiutabile in prod/staging.

Il test e' AST-based: scansiona `backend/app/**/*.py` e produce un report
deterministico delle violazioni. Pre-fix (snapshot 2026-05-21) ci sono
zero violazioni — questo blinda la baseline pulita ottenuta dopo il
cluster SSRF/TLS.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.architecture,
    pytest.mark.security,
    pytest.mark.regression,
]


APP_ROOT = Path(__file__).resolve().parents[3] / "app"

# File exempt dalla regola "no verify=False literal":
#  - `tls_policy.py` e' il gate stesso, non chiama HTTP outbound.
#  - test helper / fixture (non sotto app/) sono esclusi by-path.
_VERIFY_FALSE_ALLOWLIST: frozenset[str] = frozenset()


def _iter_python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        # __pycache__ exclusion (rglob non lo fa di default)
        if "__pycache__" in path.parts:
            continue
        yield path


def _disable_warnings_calls_at_module_level(tree: ast.Module) -> list[ast.Call]:
    """Cerca `disable_warnings(...)` come statement top-level del modulo.

    Esempio matchato (pre-fix `enrichment_service.py:17`):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    Esempio NON matchato (uso scoped lecito):
        def f():
            urllib3.disable_warnings(...)  # dentro a una funzione, OK
    """
    bad: list[ast.Call] = []
    for stmt in tree.body:
        # Solo statement top-level — il pattern problematico e' import-time.
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            func = call.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "disable_warnings"
            ):
                bad.append(call)
            elif isinstance(func, ast.Name) and func.id == "disable_warnings":
                bad.append(call)
    return bad


_HTTP_CALLABLE_HINTS = (
    "requests",  # requests.get / .post / .Session().get etc.
    "httpx",     # httpx.get / .Client().request etc.
    "_session",  # convenzione locale per shared session
    "session",
    "_client",
    "client",
)


def _looks_like_http_call(func: ast.AST) -> bool:
    """Heuristica per riconoscere chiamate HTTP outbound (no false negative
    su modo `_session.get(...)`, `requests.post(...)`, etc.).
    """
    # `<base>.method(...)` — base in HTTP_CALLABLE_HINTS
    if isinstance(func, ast.Attribute):
        base = func.value
        # `requests.get` / `httpx.post`
        if isinstance(base, ast.Name) and base.id in _HTTP_CALLABLE_HINTS:
            return True
        # `self._session.get` / `cls.client.post` — base.attr in hints
        if isinstance(base, ast.Attribute) and base.attr in _HTTP_CALLABLE_HINTS:
            return True
    return False


def _verify_false_literal_calls(tree: ast.Module) -> list[tuple[int, str]]:
    """Cerca chiamate `something_http.method(..., verify=False, ...)`
    con `False` come costante literal.
    """
    bad: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _looks_like_http_call(node.func):
            continue
        for kw in node.keywords:
            if kw.arg != "verify":
                continue
            # `verify=False` letterale
            if isinstance(kw.value, ast.Constant) and kw.value.value is False:
                bad.append((node.lineno, ast.unparse(node)[:160]))
    return bad


def test_no_module_level_disable_warnings_anywhere_in_app():
    """Invariante: nessun modulo sotto `backend/app/` chiama
    `urllib3.disable_warnings(...)` (o plain `disable_warnings(...)`) come
    statement top-level.

    Regressione: pre-fix `enrichment_service.py:17` aveva
    `urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)`
    a livello modulo, sopprimendo gli InsecureRequestWarning per l'intero
    processo Python — qualsiasi futuro `verify=False` (anche introdotto
    in un altro modulo) sarebbe risultato invisibile al security monitoring.
    """
    violations: list[str] = []
    for path in _iter_python_files(APP_ROOT):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        bad = _disable_warnings_calls_at_module_level(tree)
        for call in bad:
            violations.append(
                f"{path.relative_to(APP_ROOT.parent)}:{call.lineno} "
                f"-> {ast.unparse(call)[:160]}"
            )

    assert not violations, (
        "Chiamate `disable_warnings(...)` a livello modulo non sono "
        "permesse (sopprimono i warning globalmente). Usare "
        "`warnings.catch_warnings()` + `simplefilter` per scope ristretto.\n"
        "Violazioni:\n  - " + "\n  - ".join(violations)
    )


def test_no_verify_false_literal_in_http_calls():
    """Invariante: nessuna chiamata HTTP outbound usa `verify=False` come
    costante literal. Il flag deve essere risolto via
    `app.shared.security.tls_policy.resolve_tls_verify(...)`.

    Regressione: pre-fix `oauth.py:565` (get_arcgis_token) e
    `enrichment_retry_http.py:93,101` (call_api_with_retry) passavano
    direttamente la flag dell'utente come `verify=`, senza policy gate
    env-aware. Un admin/operatore con accesso al DB poteva spegnere TLS
    in produzione per intercettare credenziali.
    """
    violations: list[str] = []
    for path in _iter_python_files(APP_ROOT):
        rel = str(path.relative_to(APP_ROOT.parent)).replace("\\", "/")
        if rel in _VERIFY_FALSE_ALLOWLIST:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for lineno, snippet in _verify_false_literal_calls(tree):
            violations.append(f"{rel}:{lineno} -> {snippet}")

    assert not violations, (
        "Chiamate HTTP con `verify=False` literal non sono permesse: il "
        "flag deve passare per `tls_policy.resolve_tls_verify(...)` che "
        "applica la policy env-aware + opt-in. "
        "Vedi `app/core/security/tls_policy.py`.\n"
        "Violazioni:\n  - " + "\n  - ".join(violations)
    )
