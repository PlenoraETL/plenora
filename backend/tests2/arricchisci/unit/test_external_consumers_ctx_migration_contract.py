"""Contract test BE-GO-READY-TRANSFORMATION-ENGINE-020 + closure
BE-GO-READY-ORCHESTRATION-ENGINE-012-KILL-APPLY-TO-LEGACY-STATE.

Lock degli invariant residui dopo OE012 (deletion del legacy
mirror scaffold):

1. **Source-bound (worker)**: `workers/tasks/enrichment_task.py`
   NON contiene piu' direct mutation di
   `api_connection_service._current_group_id` o `_preview_limit`.
   Il typed context e' la primary (e unica) surface di tenant
   binding nel worker path.
2. **Context construction**: il task istanzia un
   `ArricchisciExecutionContext` esplicito.
3. **Threading completo**: i 4 mixin callsite nel task
   (`_load_source_project`, `_load_file_source`,
   `_load_static_values`, `_execute_rest_file`) ricevono `ctx=ctx`
   esplicito.
4. **Negative source-bound lock (repo-wide)**: NESSUN file sotto
   `app/`, `workers/`, `tests2/` (eccetto questo modulo che
   definisce il lock) puo' definire / chiamare / importare
   `apply_to_legacy_state`. La funzione e' stata cancellata da
   `app.core.arricchisci.execution_context` in OE012; il lock
   protegge contro re-introduzione.

Pre-OE012 questo file lockava anche il path "helper preserved
in canvas_orchestrator" e la concurrency-mirror tramite
`apply_to_legacy_state`. Entrambi sono stati ritirati: il
helper non esiste piu', e l'isolamento concorrente e' gia'
coperto dal frozen-dataclass test in
`test_arricchisci_execution_context_contract.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_ENRICHMENT_TASK_PY = (
    _BACKEND_ROOT / "workers" / "tasks" / "enrichment_task.py"
)


# 1. Source-bound: no more direct mutation


def _scan_assign_targets(src: str) -> list:
    """Find AST `Assign` nodes whose target is `<name>.<attr>` for
    a known set of attrs that we want to ban."""
    tree = ast.parse(src)
    forbidden_attrs = {"_current_group_id", "_preview_limit"}
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if not isinstance(tgt, ast.Attribute):
                continue
            if tgt.attr not in forbidden_attrs:
                continue
            # Check if the receiver is `api_connection_service`
            # (or any local that we want to ban). For Phase 3 we
            # ban ALL setattr on `_current_group_id` /
            # `_preview_limit` in this file — the typed context
            # boundary is the only allowed path.
            violations.append((node.lineno, tgt.attr))
    return violations


def test_enrichment_task_no_direct_singleton_mutation():
    """AST-bound: `enrichment_task.py` non deve piu' contenere
    `api_connection_service._current_group_id = ...` o
    `_preview_limit = ...` come Assign target.

    Phase 3 chiusura: il typed `ArricchisciExecutionContext` +
    `apply_to_legacy_state(...)` sono l'unica via per inizializzare
    il tenant binding.
    """
    src = _ENRICHMENT_TASK_PY.read_text(encoding="utf-8")
    violations = _scan_assign_targets(src)
    assert not violations, (
        f"REGRESSIONE BE-GO-READY-TRANSFORMATION-ENGINE-020: "
        f"`enrichment_task.py` contiene ancora direct mutation di "
        f"singleton state (Phase 3 dovrebbe averli sostituiti col "
        f"typed context): {violations}"
    )


# 2. Context construction (typed boundary primary surface)


def test_enrichment_task_imports_and_uses_typed_context():
    """`enrichment_task.py` deve importare `ArricchisciExecutionContext`
    dal modulo `execution_context` e costruire il context esplicitamente.
    Post-OE012 il worker NON chiama il legacy mirror (la funzione
    e' stata cancellata) e NON muta `svc._current_group_id`
    direttamente."""
    src = _ENRICHMENT_TASK_PY.read_text(encoding="utf-8")
    assert (
        "from app.core.arricchisci.execution_context import" in src
    ), "Atteso import del modulo execution_context."
    assert "ArricchisciExecutionContext(" in src, (
        "Atteso costruzione esplicita di ArricchisciExecutionContext."
    )


# 3. Threading completo: 4 mixin callsite ricevono ctx


def _scan_mixin_callsites(src: str) -> dict:
    """Find calls `<obj>.<attr>(...)` for the 4 reader-related
    methods; return dict attr -> list of (lineno, has_ctx_kwarg)."""
    tree = ast.parse(src)
    targets = {
        "_load_source_project",
        "_load_file_source",
        "_load_static_values",
        "_execute_rest_file",
    }
    found = {name: [] for name in targets}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute):
            continue
        if f.attr not in targets:
            continue
        has_ctx = any(kw.arg == "ctx" for kw in node.keywords)
        found[f.attr].append((node.lineno, has_ctx))
    return found


def test_enrichment_task_threads_ctx_to_mixin_callsites():
    """AST-bound: i 4 mixin callsite passano `ctx=ctx` esplicito."""
    src = _ENRICHMENT_TASK_PY.read_text(encoding="utf-8")
    found = _scan_mixin_callsites(src)
    for name, calls in found.items():
        assert calls, (
            f"Atteso almeno 1 callsite di `{name}(...)` in "
            f"enrichment_task.py."
        )
        for lineno, has_ctx in calls:
            assert has_ctx, (
                f"REGRESSIONE Phase 3: chiamata a `{name}(...)` in "
                f"enrichment_task.py:{lineno} non passa `ctx=...`. "
                f"Senza il kwarg, il reader cade sul singleton state "
                f"(KEEP_COMPAT) invece di usare il typed boundary."
            )


# 4. Negative source-bound lock (BE-GO-READY-ORCHESTRATION-ENGINE-012)


# Files that the lock MUST tolerate as "self-references" — this
# test file defines the lock and obviously names the symbol; the
# cancellation note in execution_context.py is doc-comment only.
_LOCK_SELF_FILES = {
    Path("tests2") / "arricchisci" / "unit"
    / "test_external_consumers_ctx_migration_contract.py",
    # Files where the symbol appears ONLY in prose (no def/import/call).
    # These are deliberately allowed; the violation classifier below
    # checks AST shape (defs / imports / calls), not raw substring.
}


def _scan_apply_to_legacy_state_violations() -> list[tuple[str, int, str]]:
    """Walk every .py under app/, workers/, tests2/ and report any
    AST node that DEFINES, IMPORTS, or CALLS `apply_to_legacy_state`.

    Returns a list of (relative_path_posix, lineno, kind) tuples.
    `kind` is one of {"def", "import", "call"}. Comments / strings
    do NOT count (the check parses the file with `ast`, which
    skips them).
    """
    roots = [_BACKEND_ROOT / "app", _BACKEND_ROOT / "workers", _BACKEND_ROOT / "tests2"]
    violations: list[tuple[str, int, str]] = []
    for root in roots:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            rel = py.relative_to(_BACKEND_ROOT)
            if rel in _LOCK_SELF_FILES:
                continue
            try:
                src = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(src, filename=str(py))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "apply_to_legacy_state":
                    violations.append((rel.as_posix(), node.lineno, "def"))
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == "apply_to_legacy_state":
                            violations.append((rel.as_posix(), node.lineno, "import"))
                elif isinstance(node, ast.Call):
                    f = node.func
                    if isinstance(f, ast.Name) and f.id == "apply_to_legacy_state":
                        violations.append((rel.as_posix(), node.lineno, "call"))
                    elif isinstance(f, ast.Attribute) and f.attr == "apply_to_legacy_state":
                        violations.append((rel.as_posix(), node.lineno, "call"))
    return violations


def test_apply_to_legacy_state_is_not_defined_imported_or_called_anywhere():
    """BE-GO-READY-ORCHESTRATION-ENGINE-012 negative source-bound lock.

    The legacy-mirror scaffold `apply_to_legacy_state` was deleted.
    No file under `backend/app`, `backend/workers`, or
    `backend/tests2` (except this lock module itself) may define,
    import, or call the symbol.

    Triggers a regression LOUD if a contributor re-introduces the
    helper "for safety" or copy-pastes a stale test from prior
    branches.
    """
    violations = _scan_apply_to_legacy_state_violations()
    assert not violations, (
        "BE-GO-READY-ORCHESTRATION-ENGINE-012 regression: "
        "`apply_to_legacy_state` is alive somewhere again. The "
        "legacy mirror was killed because every production reader "
        "is ctx-strict; reintroducing the helper would re-open the "
        "global-state scaffold. Offenders:\n  "
        + "\n  ".join(f"{rel}:{ln} ({kind})" for rel, ln, kind in violations)
    )
