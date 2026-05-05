"""Contract test BE-GO-READY-TRANSFORMATION-ENGINE-019 — Phase 2 mixin ctx migration.

Lock i 6 invariant della Phase 2:

1. **Signature Phase 2**: i 6 reader site
   (`_load_source_project`, `_load_file_source`, `_load_static_values`,
   `_assert_file_source_group_access`, `_resolve_file_source_id_path`,
   `_resolve_file_source_path`) accettano `ctx:
   Optional[ArricchisciExecutionContext] = None` come keyword
   argument.
2. **Phase 2 path**: quando `ctx` e' fornito, `group_id` e
   `preview_limit` vengono letti dal context tipato (NON dal
   singleton). Il typed boundary "vince".
3. **KEEP_COMPAT phase 1 fallback**: quando `ctx` non e' fornito,
   i reader fanno fallback a `getattr(self, "_current_group_id",
   None)` (singleton state). I consumer esterni
   (`enrichment_task.py`, test setup) continuano a funzionare.
4. **Tenant isolation**: ctx con un `group_id` diverso dal
   singleton → il reader usa `ctx.group_id`. Senza questo lock,
   un tenant leak post-migration sarebbe possibile.
5. **Fail-closed preservato**: ctx con `group_id=None` →
   ValueError "Contesto di sicurezza non determinabile" (stesso
   messaggio del legacy fallback).
6. **canvas_orchestrator threads ctx**: `execute_canvas_enrichment`
   passa `ctx=ctx` ai 4 callsite mixin
   (`_load_source_project`, `_load_file_source`,
   `_load_static_values`, `_execute_rest_file`). AST-bound check.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ---------------------------------------------------------------------------
# 1. Signature Phase 2 — `ctx` as kwarg on all 6 reader sites
# ---------------------------------------------------------------------------


def test_load_source_project_accepts_ctx_kwarg():
    from app.core.arricchisci.api_connection_source_loading import (
        ApiConnectionSourceLoadingMixin,
    )

    sig = inspect.signature(ApiConnectionSourceLoadingMixin._load_source_project)
    assert "ctx" in sig.parameters
    assert sig.parameters["ctx"].kind == inspect.Parameter.KEYWORD_ONLY


def test_load_file_source_accepts_ctx_kwarg():
    from app.core.arricchisci.api_connection_source_loading import (
        ApiConnectionSourceLoadingMixin,
    )

    sig = inspect.signature(ApiConnectionSourceLoadingMixin._load_file_source)
    assert "ctx" in sig.parameters
    assert sig.parameters["ctx"].kind == inspect.Parameter.KEYWORD_ONLY


def test_load_static_values_accepts_ctx_kwarg():
    from app.core.arricchisci.api_connection_source_loading import (
        ApiConnectionSourceLoadingMixin,
    )

    sig = inspect.signature(ApiConnectionSourceLoadingMixin._load_static_values)
    assert "ctx" in sig.parameters
    assert sig.parameters["ctx"].kind == inspect.Parameter.KEYWORD_ONLY


def test_assert_file_source_group_access_accepts_ctx_kwarg():
    from app.core.arricchisci.api_connection_rest_file import (
        ApiConnectionRestFileMixin,
    )

    sig = inspect.signature(
        ApiConnectionRestFileMixin._assert_file_source_group_access
    )
    assert "ctx" in sig.parameters
    assert sig.parameters["ctx"].kind == inspect.Parameter.KEYWORD_ONLY


def test_resolve_file_source_id_path_accepts_ctx_kwarg():
    from app.core.arricchisci.api_connection_rest_file import (
        ApiConnectionRestFileMixin,
    )

    sig = inspect.signature(
        ApiConnectionRestFileMixin._resolve_file_source_id_path
    )
    assert "ctx" in sig.parameters
    assert sig.parameters["ctx"].kind == inspect.Parameter.KEYWORD_ONLY


def test_resolve_file_source_path_accepts_ctx_kwarg():
    from app.core.arricchisci.api_connection_rest_file import (
        ApiConnectionRestFileMixin,
    )

    sig = inspect.signature(
        ApiConnectionRestFileMixin._resolve_file_source_path
    )
    assert "ctx" in sig.parameters
    assert sig.parameters["ctx"].kind == inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# 2 + 4. Phase 2 wins over singleton state
# ---------------------------------------------------------------------------


def test_load_static_values_uses_ctx_group_id_over_singleton(api_service):
    """Phase 2 lock: se `ctx.group_id` ≠ `svc._current_group_id`, il
    reader usa `ctx.group_id` (typed boundary wins). Senza questo
    lock un tenant leak sarebbe possibile dopo Phase 2.

    Lo static branch e' il path piu' semplice da testare (no DB,
    no SourceLoader real call) — esercita comunque
    `_resolve_group_id` source path.
    """
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    api_service._current_group_id = "grp-SINGLETON"
    ctx = ArricchisciExecutionContext(
        project_id="p1", group_id="grp-CTX", preview_limit=None,
    )

    # Mock SourceLoader().load: cattura `expected_group_id` per
    # verificare che venga passato `grp-CTX`, non `grp-SINGLETON`.
    captured = {}

    class _FakeSL:
        def load(self, ref, expected_group_id):
            captured["group_id"] = expected_group_id
            return pd.DataFrame()

    with patch(
        "app.core.artifacts.SourceLoader",
        return_value=_FakeSL(),
    ):
        api_service._load_static_values(
            {"id": "sv1", "type": "staticValues", "fields": []},
            ctx=ctx,
        )

    assert captured["group_id"] == "grp-CTX", (
        "REGRESSIONE Phase 2: ctx.group_id deve vincere sul "
        "singleton state. Senza questo lock, un context con "
        "tenant diverso non avrebbe effetto."
    )


# ---------------------------------------------------------------------------
# 3. KEEP_COMPAT fallback to singleton when ctx is None
# ---------------------------------------------------------------------------


def test_load_static_values_ignores_stale_singleton_with_explicit_ctx(api_service):
    """BE-GO-READY-TRANSFORMATION-ENGINE-023 (slice migration):
    invariant esplicito di tenant isolation per il branch static.

    Setup adversariale: il singleton ha un `_current_group_id`
    stale ("grp-LEAK") da una pre-existing execution. Il caller
    passa un context tipato con `group_id="grp-CALLER"`. Il
    branch static MUST consumare `ctx.group_id` (typed boundary
    wins) e NEVER toccare il singleton.

    Lock pre-emptivo della rimozione del legacy fallback nel
    helper `_group_id_from_ctx`: anche se future regression
    accidentalmente ripristinasse un fallback al singleton, il
    valore stale non vincerebbe contro il ctx esplicito.
    """
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    api_service._current_group_id = "grp-LEAK"
    ctx = ArricchisciExecutionContext(
        project_id="p1", group_id="grp-CALLER", preview_limit=None,
    )
    captured = {}

    class _FakeSL:
        def load(self, ref, expected_group_id):
            captured["group_id"] = expected_group_id
            return pd.DataFrame()

    with patch("app.core.artifacts.SourceLoader", return_value=_FakeSL()):
        api_service._load_static_values(
            {"id": "sv1", "type": "staticValues", "fields": []},
            ctx=ctx,
        )

    assert captured["group_id"] == "grp-CALLER", (
        "REGRESSIONE: ctx.group_id deve vincere contro stale singleton "
        f"({api_service._current_group_id!r}). Il typed boundary e' "
        "broken se questo test fallisce."
    )


def test_load_static_values_uses_static_sentinel_when_ctx_none(api_service):
    """BE-GO-READY-TRANSFORMATION-ENGINE-023 (slice migration):
    `_load_static_values` non legge piu' `_current_group_id` dal
    singleton. Quando `ctx` non e' fornito, usa il sentinel
    `"_static_"` come placeholder (lo static branch e' tenant-
    agnostic).

    Regression guard contro stale-state leak: anche se
    `svc._current_group_id` ha un valore residuo da un'altra
    esecuzione, lo static branch NON deve consumarlo. Questo
    previene la propagazione accidentale di tenant binding fra
    esecuzioni concorrenti sul singleton."""
    api_service._current_group_id = "grp-STALE-LEAK"
    captured = {}

    class _FakeSL:
        def load(self, ref, expected_group_id):
            captured["group_id"] = expected_group_id
            return pd.DataFrame()

    with patch("app.core.artifacts.SourceLoader", return_value=_FakeSL()):
        api_service._load_static_values(
            {"id": "sv1", "type": "staticValues", "fields": []},
        )  # NO ctx kwarg

    # Post-batch -023: lo static branch usa il sentinel e ignora
    # lo stale singleton state.
    assert captured["group_id"] == "_static_", (
        f"REGRESSIONE Phase 5 slice: `_load_static_values` ha letto "
        f"`_current_group_id={api_service._current_group_id!r}` invece "
        f"del sentinel `_static_` quando ctx=None. Stale-state leak "
        f"cross-execution e' tornato disponibile."
    )


def test_load_file_source_uses_ctx_preview_limit_over_singleton(api_service):
    """Phase 2 lock: `preview_limit` letto dal ctx tipato quando
    fornito, dal singleton come KEEP_COMPAT fallback."""
    from app.core.arricchisci import api_connection_source_loading as mod
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    api_service._current_group_id = "grp-A"
    api_service._preview_limit = 999  # singleton

    ctx = ArricchisciExecutionContext(
        project_id="p1", group_id="grp-A", preview_limit=3,
    )

    df_source = pd.DataFrame([{"a": i} for i in range(10)])

    class _FakeSL:
        def load(self, ref, expected_group_id):
            return df_source.copy()

    with patch("app.core.artifacts.SourceLoader", return_value=_FakeSL()), \
         patch.object(mod, "filter_visible_columns",
                      side_effect=lambda df, cols: df):
        result = api_service._load_file_source(
            {"id": "fs1", "file_source_id": "f-1"},
            ctx=ctx,
        )

    assert len(result) == 3, (
        "REGRESSIONE Phase 2: preview_limit deve essere letto da "
        "ctx (3) invece che dal singleton (999)."
    )


# ---------------------------------------------------------------------------
# 5. Fail-closed when ctx.group_id is None
# ---------------------------------------------------------------------------


def test_load_source_project_fails_closed_when_ctx_group_id_none(api_service):
    """Phase 2: ctx con `group_id=None` (progetto inesistente)
    deve fail-closed con stesso messaggio del legacy fallback."""
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    # Singleton ha un group_id valido — il ctx None DEVE vincere
    # (non leakare al fallback singleton).
    api_service._current_group_id = "grp-LEAK"
    ctx = ArricchisciExecutionContext(
        project_id="p-missing", group_id=None, preview_limit=None,
    )

    with pytest.raises(ValueError, match="Contesto di sicurezza non determinabile"):
        api_service._load_source_project(
            {"id": "src1", "source_project_id": "p-missing"},
            False,
            ctx=ctx,
        )


def test_assert_file_source_group_access_fails_closed_with_ctx_none_group(
    api_service,
):
    """Phase 2: stesso fail-closed semantico per il rest_file
    mixin."""
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    api_service._current_group_id = "grp-LEAK"
    ctx = ArricchisciExecutionContext(
        project_id="p-missing", group_id=None, preview_limit=None,
    )

    with pytest.raises(ValueError, match="Contesto di sicurezza non determinabile"):
        api_service._assert_file_source_group_access("fs-1", ctx=ctx)


# ---------------------------------------------------------------------------
# 6. canvas_orchestrator threads ctx through to mixin callsites
# ---------------------------------------------------------------------------


def _has_ctx_kwarg_in_call(node: ast.Call) -> bool:
    return any(kw.arg == "ctx" for kw in node.keywords)


def _scan_calls_with_attr_name(src: str, attr_names: set) -> dict:
    """Find all `<obj>.<attr>(...)` calls where attr in attr_names.

    Returns dict mapping attr_name -> list of (lineno, has_ctx_kwarg).
    """
    tree = ast.parse(src)
    found = {name: [] for name in attr_names}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute):
            continue
        if f.attr not in attr_names:
            continue
        found[f.attr].append((node.lineno, _has_ctx_kwarg_in_call(node)))
    return found


def test_canvas_orchestrator_passes_ctx_to_mixin_callsites():
    """AST-bound: `execute_canvas_enrichment` passa `ctx=ctx` ai 4
    mixin callsites (`_load_source_project`, `_load_file_source`,
    `_load_static_values`, `_execute_rest_file`)."""
    src = (
        _BACKEND_ROOT / "app" / "core" / "arricchisci" / "canvas_orchestrator.py"
    ).read_text(encoding="utf-8")
    targets = {
        "_load_source_project",
        "_load_file_source",
        "_load_static_values",
        "_execute_rest_file",
    }
    found = _scan_calls_with_attr_name(src, targets)
    for name in targets:
        assert found[name], (
            f"Atteso almeno 1 call site di `svc.{name}(...)` in "
            f"canvas_orchestrator.py."
        )
        # All call sites for these 4 targets must pass `ctx=`.
        for lineno, has_ctx in found[name]:
            assert has_ctx, (
                f"REGRESSIONE Phase 2: chiamata a `svc.{name}(...)` "
                f"in canvas_orchestrator.py:{lineno} non passa `ctx=...`. "
                f"Senza il kwarg, il reader cade sul singleton state "
                f"(KEEP_COMPAT) invece di usare il typed boundary."
            )


def test_canvas_orchestrator_init_returns_ctx_used_in_dispatch():
    """Lock the wiring: `init_execution_context(...)` → variabile
    `ctx` → passata ai callsite. Verificato leggendo il sorgente."""
    src = (
        _BACKEND_ROOT / "app" / "core" / "arricchisci" / "canvas_orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "ctx = init_execution_context(svc, project_id, preview_limit)" in src, (
        "Atteso assignment `ctx = init_execution_context(...)` nel "
        "body di `execute_canvas_enrichment`."
    )
