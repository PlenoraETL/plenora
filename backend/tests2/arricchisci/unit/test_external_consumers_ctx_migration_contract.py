"""Contract test BE-GO-READY-TRANSFORMATION-ENGINE-020 — Phase 3 external
consumers migration to typed `ArricchisciExecutionContext`.

Lock i 5 invariant della Phase 3 (closure di TE15-R2):

1. **Source-bound**: `workers/tasks/enrichment_task.py` NON contiene
   piu' direct mutation di `api_connection_service._current_group_id`
   o `_preview_limit`. Il typed context e' la primary surface.
2. **Context construction**: il task istanzia un
   `ArricchisciExecutionContext` esplicito.
   BE-GO-READY-TRANSFORMATION-ENGINE-030A: il worker NON chiama piu'
   `apply_to_legacy_state(...)` ne' importa il helper. Tutto il
   tenant binding nel worker path passa per `ctx=ctx` esplicito sui
   4 mixin callsite. La singleton-mutation via
   `apply_to_legacy_state` resta valida in `canvas_orchestrator`
   (preview path in-process, scope separato).
3. **Threading completo**: i 4 mixin callsite nel task
   (`_load_source_project`, `_load_file_source`,
   `_load_static_values`, `_execute_rest_file`) ricevono `ctx=ctx`
   esplicito.
4. **Helper preserved**: `apply_to_legacy_state` resta funzionale
   (usato da `canvas_orchestrator`); il test
   `test_apply_to_legacy_state_still_emits_setattr_events` lo
   verifica indipendentemente dal worker.
5. **Concurrency**: 2 task concorrenti istanziano 2 context
   distinti con tenant binding isolato (frozen dataclass + new
   ApiConnectionService instance per task).
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


# ---------------------------------------------------------------------------
# 1. Source-bound: no more direct mutation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 2. Context construction + apply_to_legacy_state usage
# ---------------------------------------------------------------------------


def test_enrichment_task_imports_and_uses_typed_context():
    """`enrichment_task.py` deve importare `ArricchisciExecutionContext`
    dal modulo `execution_context` e costruire il context esplicitamente.

    BE-GO-READY-TRANSFORMATION-ENGINE-030A: post-migrazione il worker
    NON chiama piu' `apply_to_legacy_state` ne' muta
    `svc._current_group_id` direttamente. I 4 mixin callsite sono
    ctx-aware: il ctx tipato e' l'unica via di tenant binding nel
    worker path. Vedi `test_enrichment_task_does_not_call_apply_to_legacy_state`
    sotto per il regression-guard complementare."""
    src = _ENRICHMENT_TASK_PY.read_text(encoding="utf-8")
    assert (
        "from app.core.arricchisci.execution_context import" in src
    ), "Atteso import del modulo execution_context."
    assert "ArricchisciExecutionContext(" in src, (
        "Atteso costruzione esplicita di ArricchisciExecutionContext."
    )


def test_enrichment_task_does_not_call_apply_to_legacy_state():
    """BE-GO-READY-TRANSFORMATION-ENGINE-030A regression guard:
    il worker NON deve piu' chiamare `apply_to_legacy_state(...)`
    ne' importarlo dal modulo `execution_context`. Tutto il tenant
    binding nel worker path passa per `ctx=ctx` thread esplicito ai
    4 mixin callsite (TE-020). La singleton-mutation via
    `apply_to_legacy_state` resta valida in
    `app.core.arricchisci.canvas_orchestrator` per il preview path
    in-process (scope separato).

    AST-bound: nessun `Call` con `func=Name('apply_to_legacy_state')`
    ne' nessun `ImportFrom` con `name='apply_to_legacy_state'`.
    """
    src = _ENRICHMENT_TASK_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)

    forbidden_calls = []
    forbidden_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "apply_to_legacy_state":
                forbidden_calls.append(node.lineno)
            elif isinstance(f, ast.Attribute) and f.attr == "apply_to_legacy_state":
                forbidden_calls.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "apply_to_legacy_state":
                    forbidden_imports.append(node.lineno)

    assert not forbidden_calls, (
        f"BE-GO-READY-TRANSFORMATION-ENGINE-030A regression: "
        f"`enrichment_task.py` chiama `apply_to_legacy_state(...)` "
        f"alle linee {forbidden_calls}. Il worker NON deve piu' "
        f"mutare `_current_group_id` via legacy state mirror — "
        f"tutto il tenant binding e' via `ctx=ctx` esplicito ai "
        f"4 mixin callsite."
    )
    assert not forbidden_imports, (
        f"BE-GO-READY-TRANSFORMATION-ENGINE-030A regression: "
        f"`enrichment_task.py` importa `apply_to_legacy_state` "
        f"alle linee {forbidden_imports}. Rimuovere l'import: il "
        f"worker non lo usa piu'."
    )


# ---------------------------------------------------------------------------
# 3. Threading completo: 4 mixin callsite ricevono ctx
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 4. Behavior preserved: existing tenant-binding lock tests still apply
# ---------------------------------------------------------------------------


def test_apply_to_legacy_state_still_emits_setattr_events():
    """Sanity check del path di compatibilita': il
    `FakeApiConnectionService` (test helper) registra ogni setattr
    di `_current_group_id`. `apply_to_legacy_state` fa 2 setattr
    su `_current_group_id` (None reset + ctx.group_id) — entrambi
    devono apparire nel call_log come `setattr:_current_group_id`.

    Lock comportamentale di base: senza questo invariant, i lock
    test pre-existing in
    `test_enrichment_task_tenant_binding_lock.py` non potrebbero
    osservare il binding del tenant.
    """
    from tests2._helpers.celery_eager import FakeApiConnectionService
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
        apply_to_legacy_state,
    )

    fake = FakeApiConnectionService()
    # Snapshot del log dopo init (FakeApiConnectionService.__init__
    # registra 1 setattr di `_current_group_id = None` come effetto
    # di costruzione).
    pre_apply_count = len(fake._call_log)

    ctx = ArricchisciExecutionContext(
        project_id="p1",
        group_id="grp-A",
        preview_limit=None,
    )
    apply_to_legacy_state(fake, ctx)

    post_apply_events = [
        ev for ev in fake._call_log[pre_apply_count:]
        if ev[0] == "setattr:_current_group_id"
    ]
    # 2 events da apply_to_legacy_state: reset a None + ctx.group_id.
    # `_preview_limit` viene scritto prima, ma non e' tracciato dal
    # FakeApiConnectionService (registra solo `_current_group_id`).
    assert len(post_apply_events) == 2
    assert post_apply_events[0] == ("setattr:_current_group_id", None)
    assert post_apply_events[1] == ("setattr:_current_group_id", "grp-A")
    assert fake._current_group_id == "grp-A"


# ---------------------------------------------------------------------------
# 5. Concurrency: two contexts isolated even if same singleton class
# ---------------------------------------------------------------------------


def test_two_task_contexts_stay_isolated_with_typed_boundary():
    """Phase 3 lock: due esecuzioni concorrenti del task
    istanziano 2 `ApiConnectionService` distinti + 2 context
    frozen distinti. Il typed boundary garantisce che il tenant
    binding non leak fra esecuzioni anche con singleton class
    condivisa."""
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
        apply_to_legacy_state,
    )
    from tests2._helpers.celery_eager import FakeApiConnectionService

    svc_a = FakeApiConnectionService()
    svc_b = FakeApiConnectionService()
    ctx_a = ArricchisciExecutionContext(
        project_id="p-A", group_id="grp-A", preview_limit=10,
    )
    ctx_b = ArricchisciExecutionContext(
        project_id="p-B", group_id="grp-B", preview_limit=None,
    )

    apply_to_legacy_state(svc_a, ctx_a)
    apply_to_legacy_state(svc_b, ctx_b)

    # Tenant binding isolato sui singleton instance distinti.
    assert svc_a._current_group_id == "grp-A"
    assert svc_b._current_group_id == "grp-B"
    assert svc_a._preview_limit == 10
    assert svc_b._preview_limit is None
    # I context sono immutabili — non possono essere alterati post-
    # construction (lock pre-existing in
    # test_arricchisci_execution_context_contract).
    assert ctx_a is not ctx_b
