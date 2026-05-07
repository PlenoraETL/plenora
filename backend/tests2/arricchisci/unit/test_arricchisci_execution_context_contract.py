"""
Contract test BE-GO-READY-TRANSFORMATION-ENGINE-015 — typed

execution context per arricchisci canvas execution.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import psycopg2
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


# 1. Module exposure + re-export from canvas_orchestrator


def test_execution_context_module_exposes_typed_boundary():
    """Post-OE012: il modulo espone il typed shape + il builder.
    `apply_to_legacy_state` e' stato cancellato (legacy mirror dead
    write); il negative lock vive in
    `test_external_consumers_ctx_migration_contract.py`."""
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
        build_execution_context,
    )

    assert ArricchisciExecutionContext.__name__ == "ArricchisciExecutionContext"
    assert callable(build_execution_context)


def test_canvas_orchestrator_reexports_context_boundary():
    """Consumer esterni (es. `enrichment_task.py` future migration)
    devono poter importare il typed boundary direttamente da
    `canvas_orchestrator` senza dover scoprire un secondo modulo."""
    from app.core.arricchisci import canvas_orchestrator as co
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext as CtxFromOrigin,
        build_execution_context as build_from_origin,
    )

    assert co.ArricchisciExecutionContext is CtxFromOrigin
    assert co.build_execution_context is build_from_origin


# 2. Frozen / immutable


def test_context_is_frozen_dataclass():
    """`@dataclass(frozen=True)` ⇒ setattr solleva."""
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    ctx = ArricchisciExecutionContext(
        project_id="p1", group_id="grp-A", preview_limit=None,
    )
    with pytest.raises(FrozenInstanceError):
        ctx.group_id = "grp-MALICIOUS"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        ctx.preview_limit = 99  # type: ignore[misc]


def test_context_fields_typed():
    """Lock di shape: 3 campi tipati nel dataclass."""
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    field_names = {f.name for f in fields(ArricchisciExecutionContext)}
    assert field_names == {"project_id", "group_id", "preview_limit"}


# 3. Concurrency / isolation


def test_two_contexts_are_isolated():
    """Due esecuzioni concorrenti producono context distinti senza
    shared mutable state. Non possono interferirsi a vicenda."""
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    ctx_a = ArricchisciExecutionContext(
        project_id="p-A", group_id="grp-A", preview_limit=10,
    )
    ctx_b = ArricchisciExecutionContext(
        project_id="p-B", group_id="grp-B", preview_limit=None,
    )
    assert ctx_a.group_id == "grp-A"
    assert ctx_b.group_id == "grp-B"
    assert ctx_a.preview_limit == 10
    assert ctx_b.preview_limit is None
    assert ctx_a is not ctx_b


# 4. Builder fail-closed on DB error


def test_build_context_raises_execution_context_error_on_db_error(monkeypatch):
    """`psycopg2.OperationalError` → `ExecutionContextError` con
    chain preservata. Stesso contract del legacy
    `init_execution_context`."""
    from app.core.arricchisci import execution_context as ec_mod  # noqa: F401
    from app.shared.exceptions import ExecutionContextError

    original = psycopg2.OperationalError("pool exhausted")

    def _boom(_pid):
        raise original

    monkeypatch.setattr("app.core.arricchisci.execution_context._get_project", _boom)

    with pytest.raises(ExecutionContextError) as exc_info:
        ec_mod.build_execution_context("proj-1", preview_limit=None)
    assert exc_info.value.project_id == "proj-1"
    assert exc_info.value.original_error is original
    assert exc_info.value.__cause__ is original


# 5. Builder happy path + missing project


def test_build_context_happy_path(monkeypatch):
    from app.core.arricchisci import execution_context as ec_mod

    monkeypatch.setattr(
        "app.core.arricchisci.execution_context._get_project",
        lambda pid: {"id": pid, "group_id": "grp-A", "name": "p1"},
    )

    ctx = ec_mod.build_execution_context("proj-1", preview_limit=42)
    assert ctx.project_id == "proj-1"
    assert ctx.group_id == "grp-A"
    assert ctx.preview_limit == 42


def test_build_context_missing_project_yields_none_group(monkeypatch):
    """Progetto inesistente → `group_id=None` (ramo dati legittimo,
    non eccezione). I mixin downstream sollevano "Contesto di
    sicurezza non determinabile"."""
    from app.core.arricchisci import execution_context as ec_mod  # noqa: F401

    monkeypatch.setattr("app.core.arricchisci.execution_context._get_project", lambda pid: None)
    ctx = ec_mod.build_execution_context("proj-unknown", preview_limit=None)
    assert ctx.group_id is None
    assert ctx.preview_limit is None


# 6. init_execution_context returns the typed context (no singleton write)


def test_init_execution_context_returns_typed_context(api_service, monkeypatch):
    """Post-OE011: `init_execution_context` returns the typed
    `ArricchisciExecutionContext` and does NOT mutate
    `svc._current_group_id` / `svc._preview_limit`. The
    pre-OE011 KEEP_COMPAT mirror was retired -- production
    mixin readers are all ctx-strict, so the singleton write was
    dead-write. The standalone function `apply_to_legacy_state`
    remains available for callers that explicitly want the
    mirror (test surface only post-OE011)."""
    from app.core.arricchisci import canvas_orchestrator as co
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    monkeypatch.setattr(
        "app.core.arricchisci.execution_context._get_project",
        lambda pid: {"id": pid, "group_id": "grp-Z", "name": "px"},
    )

    ctx = co.init_execution_context(api_service, "proj-x", preview_limit=7)
    assert isinstance(ctx, ArricchisciExecutionContext)
    assert ctx.project_id == "proj-x"
    assert ctx.group_id == "grp-Z"
    assert ctx.preview_limit == 7


def test_init_execution_context_does_not_write_singleton_state(
    api_service, monkeypatch,
):
    """OE011 explicit cut lock: `init_execution_context` MUST
    NOT mutate `svc._current_group_id` or `svc._preview_limit`.
    Pre-OE011 the function called `apply_to_legacy_state(svc,
    ctx)` to mirror values; post-OE011 production mixin readers
    are ctx-strict, so the mirror was dead-write. This test
    catches a regression where someone re-adds the mirror "for
    safety" without checking that production reads are
    ctx-strict.

    We pre-set a STALE singleton value before calling
    `init_execution_context` and assert the function did NOT
    overwrite it -- the singleton is no longer the orchestrator's
    concern."""
    from app.core.arricchisci import canvas_orchestrator as co

    monkeypatch.setattr(
        "app.core.arricchisci.execution_context._get_project",
        lambda pid: {"id": pid, "group_id": "grp-FRESH", "name": "p"},
    )

    # Pre-set stale singleton state.
    api_service._current_group_id = "grp-STALE"
    api_service._preview_limit = 9999

    ctx = co.init_execution_context(api_service, "proj-x", preview_limit=42)

    # The typed ctx carries the FRESH binding.
    assert ctx.group_id == "grp-FRESH"
    assert ctx.preview_limit == 42
    # The singleton is NOT touched -- stale value survives.
    # That's the OE011 invariant: production mixin readers
    # consume `ctx` exclusively, so the singleton state is
    # irrelevant. A stale singleton cannot leak into the new
    # run because no production reader consults it.
    assert api_service._current_group_id == "grp-STALE"
    assert api_service._preview_limit == 9999


# 7. (RITIRATO post-OE012) apply_to_legacy_state mirroring tests
#
# Pre-OE012 questa sezione locckava due test che esercitavano la
# funzione `apply_to_legacy_state` in modalita' standalone:
#   - test_apply_to_legacy_state_mirrors_context
#   - test_apply_to_legacy_state_with_none_group_clears_singleton
# Entrambi sono stati eliminati insieme alla funzione. Lo state
# `_current_group_id` / `_preview_limit` sul singleton non e' piu'
# letto da nessun reader di produzione (OE011 ha reso ctx-strict
# i mixin), quindi non ha piu' senso lockarne la mirror.
# Il negative source-bound lock vive in
# `tests2/arricchisci/unit/test_external_consumers_ctx_migration_contract.py`.
