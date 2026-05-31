"""
Contract test AST-based — coverage @audit_action sugli endpoint sensibili.

Scope (Contract:
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


_BACKEND = Path(__file__).resolve().parents[3]
_CORE = _BACKEND / "app" / "core"


# (file relativo a backend/, nome funzione, action attesa nel decorator)
# Nota: per gli endpoint dove `target_param` non e' nel path-param ma in un
# body Pydantic (es. `request: WriteRequest` per /features/write), la lista
# si limita a verificare la presenza del decorator + l'action — non il
# target_param, perche' assente by-design (limite dell'extractor, vedi
# `audit_action.py:190`).
_REQUIRED_AUDIT_DECORATORS: list[tuple[str, str, str]] = [
    # connections (tutti e 4 mutating + test)
    ("connections/router.py", "create_connection",   "connection.create"),
    ("connections/router.py", "update_connection",   "connection.update"),
    ("connections/router.py", "delete_connection",   "connection.delete"),
    ("connections/router.py", "test_connection",     "connection.test"),
    # features ArcGIS (4 conn + write + create-service)
    ("features/routes.py",    "create_connection",   "arcgis.connection.create"),
    ("features/routes.py",    "update_connection",   "arcgis.connection.update"),
    ("features/routes.py",    "delete_connection",   "arcgis.connection.delete"),
    ("features/routes.py",    "test_connection",     "arcgis.connection.test"),
    ("features/routes.py",    "write_features",      "arcgis.feature.write"),
    ("features/routes.py",    "create_service",      "arcgis.service.create"),
    # scheduler (lifecycle 6)
    ("scheduler/router.py",   "create_schedule",         "schedule.create"),
    ("scheduler/router.py",   "update_schedule",         "schedule.update"),
    ("scheduler/router.py",   "delete_schedule",         "schedule.delete"),
    ("scheduler/router.py",   "toggle_schedule",         "schedule.toggle"),
    ("scheduler/router.py",   "update_schedule_catchup", "schedule.catchup"),
    ("scheduler/router.py",   "run_schedule_now",        "schedule.run_now"),
    # eseguibili (CRUD + analyze + execute)
    ("eseguibili/router.py",  "create_eseguibile",   "eseguibile.create"),
    ("eseguibili/router.py",  "update_eseguibile",   "eseguibile.update"),
    ("eseguibili/router.py",  "delete_eseguibile",   "eseguibile.delete"),
    ("eseguibili/router.py",  "analyze_upload",      "eseguibile.analyze_upload"),
    ("eseguibili/router.py",  "execute_eseguibile",  "eseguibile.execute"),
    # admin_backups (trigger superadmin)
    ("admin_backups/router.py", "backup_trigger_endpoint", "admin.backup.trigger"),
    # orchestrator (cancel + cleanup full)
    ("orchestrator/router.py", "cancel_task",              "task.cancel"),
    ("orchestrator/router.py", "cleanup_task_completely",  "task.cleanup_complete"),
]


def _decorator_action(dec: ast.expr) -> str | None:
    """Estrae il valore di `action=...` da un decorator `audit_action(...)`.

    Ritorna None se il decorator non e' `audit_action(...)` o se l'argomento
    `action` non e' una stringa letterale (caso pratico improbabile ma
    difensivo: vogliamo solo riconoscere il pattern usato nel repo).
    """
    if not isinstance(dec, ast.Call):
        return None
    fn = dec.func
    name = fn.id if isinstance(fn, ast.Name) else (
        fn.attr if isinstance(fn, ast.Attribute) else None
    )
    if name != "audit_action":
        return None
    # action puo' essere positional (args[0]) o keyword.
    if dec.args:
        first = dec.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    for kw in dec.keywords:
        if kw.arg == "action" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _collect_function_decorators(path: Path) -> dict[str, list[str | None]]:
    """Mappa nome funzione -> lista di action audit (None se decorator estraneo).

    Cattura sia `def` che `async def`. Una stessa funzione ha tipicamente
    1-2 decorator (router + audit_action); l'helper ritorna gli `action`
    solo per quelli che riconosce come `audit_action(...)`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, list[str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            actions = [_decorator_action(d) for d in node.decorator_list]
            out[node.name] = [a for a in actions if a is not None]
    return out


@pytest.mark.parametrize("rel_path,func_name,expected_action", _REQUIRED_AUDIT_DECORATORS)
def test_endpoint_has_audit_action(rel_path: str, func_name: str, expected_action: str):
    """Ciascun endpoint sensibile deve avere @audit_action con action attesa."""
    file_path = _CORE / rel_path
    assert file_path.exists(), f"Sorgente atteso non trovato: {file_path}"

    by_name = _collect_function_decorators(file_path)
    assert func_name in by_name, (
        f"Funzione '{func_name}' non trovata in {rel_path}: o e' stata "
        f"rinominata, o il file e' stato spostato. Aggiornare la lista "
        f"_REQUIRED_AUDIT_DECORATORS coerentemente."
    )
    actions = by_name[func_name]
    assert actions, (
        f"{rel_path}::{func_name} non ha @audit_action. "
        f"Atteso action='{expected_action}'. Vedi P1-01 in "
        f"AUDIT_REPORT_security.md per il razionale."
    )
    assert expected_action in actions, (
        f"{rel_path}::{func_name} ha @audit_action ma con action diversa: "
        f"vista {actions}, attesa '{expected_action}'."
    )
