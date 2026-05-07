"""
Contract test BE-GO-READY-TRANSFORMATION-ENGINE-019 — Phase 2 mixin ctx migration.

Lock i 6 invariant della Phase 2:
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


# 1. Signature Phase 2 — `ctx` as kwarg on all 6 reader sites


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


# 2 + 4. Phase 2 wins over singleton state


def test_load_static_values_uses_ctx_group_id_over_singleton(api_service):
    """Phase 2 lock: se `ctx.group_id` ≠ `svc._current_group_id`, il
    reader usa `ctx.group_id` (typed boundary wins). Senza questo
    lock un tenant leak sarebbe possibile dopo Phase 2.

    Lo static branch e' il path piu' semplice da testare (no DB,
    no SourceLoader real call) — esercita comunque la `group_id`
    resolution path via `_group_id_from_ctx(ctx)` (TE-023 strict).
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


# 3. KEEP_COMPAT fallback to singleton when ctx is None


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


def test_load_file_source_ignores_stale_preview_limit_when_ctx_preview_none(api_service):
    """BE-GO-READY-TRANSFORMATION-ENGINE-025 (slice migration) +
    -030B (group_id strict promotion): `_load_file_source` non
    legge piu' `_preview_limit` ne' `_current_group_id` dal
    singleton. Quando `ctx.preview_limit is None`, il preview_limit
    e' considerato non-richiesto (no truncation) — anche se
    `svc._preview_limit` ha un valore stale da una pre-existing
    execution.

    Regression guard contro stale-state cross-execution leak: un
    `_preview_limit=3` lasciato da T1 non deve truncare
    silenziosamente l'output di T2 che passa un ctx con
    `preview_limit=None`. Pre-batch -025 questo era un fail-soft
    behavior che dipendeva dall'ordine di esecuzione sul singleton.

    Storia: pre-TE-030B il test usava `ctx=None` (singleton fallback
    per group_id). Post-TE-030B il fallback singleton e' stato
    rimosso, quindi il test passa ctx con `group_id` valido +
    `preview_limit=None` per esercitare il medesimo invariant
    (stale singleton preview_limit ignored).
    """
    from app.core.arricchisci import api_connection_source_loading as mod
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    # Stale singleton state da una pre-existing execution.
    api_service._current_group_id = "stale-grp-LEAK"
    api_service._preview_limit = 3

    ctx = ArricchisciExecutionContext(
        project_id="p1", group_id="grp-A", preview_limit=None,
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

    assert len(result) == 10, (
        f"REGRESSIONE Phase 5 slice -025: stale `_preview_limit=3` "
        f"sul singleton ha truncato l'output ({len(result)} righe) "
        f"anche con ctx.preview_limit=None. Cross-execution leak "
        f"ripristinato."
    )


def test_load_file_source_ctx_preview_limit_wins_over_stale_singleton(api_service):
    """BE-GO-READY-TRANSFORMATION-ENGINE-025 (slice migration):
    invariant esplicito di tenant/run isolation per il preview_limit.

    Setup adversariale: il singleton ha `_preview_limit = 999`
    stale. Il caller passa `ctx.preview_limit = 5`. La truncation
    deve usare 5 (typed boundary), NON 999 (singleton stale).

    Lock pre-emptivo della rimozione del fallback nel helper
    `_preview_limit_from_ctx`: anche se future regression
    accidentalmente ripristinasse un fallback al singleton, il
    valore stale non vincerebbe contro il ctx esplicito.
    """
    from app.core.arricchisci import api_connection_source_loading as mod
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    api_service._current_group_id = "grp-A"
    api_service._preview_limit = 999  # stale singleton

    ctx = ArricchisciExecutionContext(
        project_id="p1", group_id="grp-A", preview_limit=5,
    )

    df_source = pd.DataFrame([{"a": i} for i in range(20)])

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

    assert len(result) == 5, (
        f"REGRESSIONE: ctx.preview_limit=5 deve vincere contro stale "
        f"singleton _preview_limit={api_service._preview_limit}. Got "
        f"{len(result)} rows."
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


# 5. Fail-closed when ctx.group_id is None


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


# 6. canvas_orchestrator threads ctx through to mixin callsites


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


# rest_file legacy helper removal (BE-GO-READY-TRANSFORMATION-ENGINE-027)


def test_rest_file_legacy_resolve_helper_is_removed():
    """BE-GO-READY-TRANSFORMATION-ENGINE-027 lock:
    `_resolve_rest_file_group_id` (legacy helper introdotto in
    -019, sostituito da `_extract_file_source_group_id` /
    `_extract_project_source_group_id` strict helpers in -024/-026)
    NON deve piu' esistere come definizione attiva nel modulo.

    Verifica AST-bound: nessun `def _resolve_rest_file_group_id`
    a livello modulo. Le menzioni in docstring/commenti restano
    ammesse (documentazione storica della removal).

    Rationale: l'helper legacy mescolava il path Phase 2 (typed
    ctx) con il fallback singleton in modo *implicito*. La sua
    presenza, anche dead, sarebbe un foot-gun: future modifiche
    potrebbero re-introdurla come "scorciatoia" perdendo il
    fail-closed strict per i path file_source/project_source. Il
    lock previene la reintroduzione."""
    import ast

    src_path = (
        _BACKEND_ROOT / "app" / "core" / "arricchisci"
        / "api_connection_rest_file.py"
    )
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    forbidden_def_names = {"_resolve_rest_file_group_id"}

    violations = []
    for node in ast.walk(tree):
        # Catch both module-level FunctionDef and any nested
        # def. We don't expect nested defs of this name, but be
        # defensive against future refactors.
        if isinstance(node, ast.FunctionDef) and node.name in forbidden_def_names:
            violations.append((node.name, node.lineno))

    assert not violations, (
        f"REGRESSIONE BE-GO-READY-TRANSFORMATION-ENGINE-027: "
        f"helper legacy reintrodotto: {violations}. "
        f"Usare invece `_extract_file_source_group_id` (strict, "
        f"per file_source path) o `_extract_project_source_group_id` "
        f"(strict, per project_source path). Il legacy helper "
        f"mescolava Phase 2 (ctx) e fallback singleton in modo "
        f"implicito — foot-gun rimosso intenzionalmente in -027."
    )


def test_rest_file_strict_helpers_still_present():
    """Lock complementare al test sopra: assicura che le
    rimpiazzanti strict (`_extract_file_source_group_id`,
    `_extract_project_source_group_id`) siano ancora presenti
    come definizioni a livello modulo. Senza questo lock, una
    rimozione accidentale di entrambe (legacy + strict) lascierebbe
    il file privo di un boundary di resolve esplicito."""
    import ast

    src_path = (
        _BACKEND_ROOT / "app" / "core" / "arricchisci"
        / "api_connection_rest_file.py"
    )
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    expected = {
        "_extract_file_source_group_id",
        "_extract_project_source_group_id",
    }
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in expected:
            found.add(node.name)

    missing = expected - found
    assert not missing, (
        f"Strict helper rimpiazzanti mancanti: {missing}. "
        f"Atteso: i 2 helper ctx-strict introdotti in -024/-026 "
        f"per i path file_source / project_source."
    )


# BE-GO-READY-TRANSFORMATION-ENGINE-029 — source-loading migratable locks


def test_te029_source_loading_migratable_tests_use_ctx_and_stale_singleton():
    """Source-bound guard: i 6 test classificati come MIGRATABLE da
    TE-028 sono stati migrati a ctx esplicito in TE-029. Ognuno deve:
      1. usare la fixture `_make_service_no_singleton()` (NON
         `_make_service(group_id=...)`);
      2. settare `_current_group_id = "stale-grp-LEAK"` come decoy;
      3. passare `ctx=_ctx_grp(...)` al call site del SUT.

    Senza questa coverage, un futuro refactor potrebbe rollback i 6
    test al pattern singleton-source-of-truth, indebolendo la
    Phase-2 ctx coverage in source loading.

    I 6 test target (in `test_api_connector_source_loading_core.py`):
      - `test_load_source_project_propagates_group_id_via_source_loader`
      - `test_load_source_project_blocks_cross_tenant_via_loader`
      - `test_load_source_project_preserves_cross_tenant_detail_in_message`
      - `test_load_source_project_distinguishes_project_not_found_from_cross_tenant`
      - `test_resolve_file_source_path_project_branch_passes_through_source_loader`
      - `test_resolve_file_source_path_blocks_cross_tenant_project_source`
    """
    src_path = (
        _BACKEND_ROOT / "tests2" / "security" / "unit"
        / "test_api_connector_source_loading_core.py"
    )
    text = src_path.read_text(encoding="utf-8")
    tree = ast.parse(text)

    migrated_tests = {
        "test_load_source_project_propagates_group_id_via_source_loader",
        "test_load_source_project_blocks_cross_tenant_via_loader",
        "test_load_source_project_preserves_cross_tenant_detail_in_message",
        "test_load_source_project_distinguishes_project_not_found_from_cross_tenant",
        "test_resolve_file_source_path_project_branch_passes_through_source_loader",
        "test_resolve_file_source_path_blocks_cross_tenant_project_source",
    }

    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in migrated_tests:
            found[node.name] = ast.unparse(node)

    missing = migrated_tests - set(found.keys())
    assert not missing, (
        f"TE-029 migrated source-loading tests missing in "
        f"test_api_connector_source_loading_core.py: {sorted(missing)}"
    )

    for test_name, body_src in found.items():
        assert "_make_service_no_singleton()" in body_src, (
            f"{test_name}: must use `_make_service_no_singleton()` "
            f"fixture — TE-029 migration moves these tests off the "
            f"singleton-pre-set pattern. Got body:\n{body_src}"
        )
        # ast.unparse normalizes string quoting; accept both forms.
        assert (
            'svc._current_group_id = "stale-grp-LEAK"' in body_src
            or "svc._current_group_id = 'stale-grp-LEAK'" in body_src
        ), (
            f"{test_name}: must set svc._current_group_id = "
            f"'stale-grp-LEAK' to prove ctx wins over stale singleton. "
            f"Got body:\n{body_src}"
        )
        assert "ctx=_ctx_grp(" in body_src, (
            f"{test_name}: must pass `ctx=_ctx_grp(group_id=...)` to "
            f"the SUT call. Got body:\n{body_src}"
        )


def test_te030b_source_loading_legacy_helpers_removed():
    """BE-GO-READY-TRANSFORMATION-ENGINE-030B regression guard: i 2
    helper legacy in `api_connection_source_loading.py` —
    `_resolve_group_id` (ctx-or-singleton-fallback) e
    `_resolve_preview_limit` (idem per preview) — DEVONO essere stati
    rimossi.

    Motivo: post-TE-030A (worker no-singleton-mutation) + TE-030B
    (3 critical fail-closed test migrati a ctx-strict + 2 callsite
    promossi a `_group_id_from_ctx`), entrambi i legacy helper sono
    dead code. La loro re-introduzione riaprirebbe il leak singleton
    cross-execution che le strict variant chiudono.

    Pre-TE-030B questo lock asseriva il contrario (helper MUST
    exist) per impedire rimozione prematura mentre il worker /
    fail-closed locks dipendevano ancora dalla fallback. Post-TE-030B
    il lock e' invertito.
    """
    src_path = (
        _BACKEND_ROOT / "app" / "core" / "arricchisci"
        / "api_connection_source_loading.py"
    )
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    forbidden = {"_resolve_group_id", "_resolve_preview_limit"}
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in forbidden:
            violations.append((node.name, node.lineno))

    assert not violations, (
        f"BE-GO-READY-TRANSFORMATION-ENGINE-030B regression: legacy "
        f"helper(s) re-introdotti in api_connection_source_loading.py: "
        f"{violations}. Questi helper mescolano Phase-2 ctx con "
        f"singleton fallback in modo implicito (foot-gun cross-tenant "
        f"leak). Usare `_group_id_from_ctx(ctx)` / "
        f"`_preview_limit_from_ctx(ctx)` strict invece."
    )


def test_te030b_source_loading_callsites_use_strict_helper():
    """Positive lock TE-030B: i 2 callsite production
    `_load_source_project` e `_load_file_source` in
    `api_connection_source_loading.py` DEVONO chiamare
    `_group_id_from_ctx(ctx)` (strict) e NON nessun helper legacy
    rimosso.

    Senza questo lock, un futuro refactor potrebbe rollback i
    callsite a un nuovo helper ctx-or-fallback re-introducendo lo
    stesso pattern foot-gun.
    """
    src_path = (
        _BACKEND_ROOT / "app" / "core" / "arricchisci"
        / "api_connection_source_loading.py"
    )
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    target_methods = {"_load_source_project", "_load_file_source"}
    seen_strict_call = {name: False for name in target_methods}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in target_methods:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            if isinstance(f, ast.Name) and f.id == "_group_id_from_ctx":
                seen_strict_call[node.name] = True
                break

    missing = [name for name, ok in seen_strict_call.items() if not ok]
    assert not missing, (
        f"TE-030B regression: i seguenti callsite non chiamano "
        f"`_group_id_from_ctx(ctx)` strict per la resolve del "
        f"group_id: {missing}. Verificare che la promozione strict "
        f"sia preservata."
    )


# BE-GO-READY-TRANSFORMATION-ENGINE-032 — ctx required signatures


def _get_signature_from_mixin(method_name: str):
    """Helper: ottiene la signature di un metodo del mixin
    `ApiConnectionSourceLoadingMixin` evitando l'inheritance MRO
    via `ApiConnectionService` (che istanzia DB pool / logger).
    """
    from app.core.arricchisci.api_connection_source_loading import (
        ApiConnectionSourceLoadingMixin,
    )
    return inspect.signature(getattr(ApiConnectionSourceLoadingMixin, method_name))


def test_te032_load_source_project_ctx_is_required_keyword_only():
    """BE-GO-READY-TRANSFORMATION-ENGINE-032: `ctx` su
    `_load_source_project` e' un keyword-only **required**
    parameter — niente default `None`. I 3 production callsite
    (canvas_orchestrator, executor.preview, enrichment_task) e
    tutti i test callsite passano `ctx=ctx` esplicito post-
    TE-019/-020/-030A/-030B/-031.

    Lock contro rollback a `Optional` con default None: senza,
    un futuro refactor potrebbe accidentalmente accettare
    callsite senza ctx, riaprendo il foot-gun di tenant binding
    via singleton fallback (chiuso da TE-030B).
    """
    sig = _get_signature_from_mixin("_load_source_project")
    assert "ctx" in sig.parameters, (
        "`ctx` parameter mancante da `_load_source_project` signature."
    )
    p = sig.parameters["ctx"]
    assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
        f"`ctx` deve essere KEYWORD_ONLY, trovato kind={p.kind}."
    )
    assert p.default is inspect.Parameter.empty, (
        f"REGRESSIONE TE-032: `ctx` su `_load_source_project` ha "
        f"default `{p.default!r}` — deve essere required (no default). "
        f"Cambiare la signature a `ctx: ArricchisciExecutionContext` "
        f"senza default."
    )


def test_te032_load_file_source_ctx_is_required_keyword_only():
    """BE-GO-READY-TRANSFORMATION-ENGINE-032: `ctx` su
    `_load_file_source` e' un keyword-only **required** parameter."""
    sig = _get_signature_from_mixin("_load_file_source")
    assert "ctx" in sig.parameters, (
        "`ctx` parameter mancante da `_load_file_source` signature."
    )
    p = sig.parameters["ctx"]
    assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
        f"`ctx` deve essere KEYWORD_ONLY, trovato kind={p.kind}."
    )
    assert p.default is inspect.Parameter.empty, (
        f"REGRESSIONE TE-032: `ctx` su `_load_file_source` ha default "
        f"`{p.default!r}` — deve essere required (no default)."
    )


def test_te032_load_static_values_keeps_optional_ctx():
    """BE-GO-READY-TRANSFORMATION-ENGINE-032: a differenza dei 2
    sibling, `_load_static_values` mantiene `ctx: Optional[...] =
    None`.

    Rationale: lo static branch e' tenant-agnostic; quando ctx
    e' None usa il sentinel `"_static_"` come placeholder. Due
    regression test esplicitamente lockano il behavior `ctx=None`
    (cfr. `test_load_static_values_uses_static_sentinel_when_ctx_none`
    in questo modulo, e `test_load_static_values_works_without_db_or_user`
    in `tests2/security/unit/test_api_connector_source_loading_core.py`).

    Cambiare static_values a required romperebbe quei lock.
    """
    sig = _get_signature_from_mixin("_load_static_values")
    assert "ctx" in sig.parameters, (
        "`ctx` parameter mancante da `_load_static_values` signature."
    )
    p = sig.parameters["ctx"]
    assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
        f"`ctx` deve essere KEYWORD_ONLY, trovato kind={p.kind}."
    )
    assert p.default is None, (
        f"REGRESSIONE TE-032: `ctx` su `_load_static_values` deve "
        f"avere `default=None` (Optional) per preservare il sentinel "
        f"behavior con ctx=None. Trovato default={p.default!r}."
    )


def test_te032_no_app_workers_callsite_omits_ctx_for_tenant_loaders():
    """BE-GO-READY-TRANSFORMATION-ENGINE-032: AST-bound regression
    guard. Nessuna call a `_load_source_project` o
    `_load_file_source` in `app/` o `workers/` deve omettere il
    kwarg `ctx=`. Inclusi i call site su `api_connection_service`
    (singleton import) e su `svc` (instance-level).

    Senza questo guard, un futuro callsite production potrebbe
    accidentalmente omettere ctx → TypeError al runtime per il
    keyword-only required, oppure (peggio) un wrapper che
    accetta `ctx=None` di default re-introdurrebbe il foot-gun.

    `_load_static_values` e' explicitly excluded perche'
    Optional per design (sentinel `"_static_"` quando ctx=None).
    """
    roots = ["app", "workers"]
    targets = {"_load_source_project", "_load_file_source"}
    violations: list = []
    for root in roots:
        root_path = _BACKEND_ROOT / root
        for py_path in root_path.rglob("*.py"):
            try:
                tree = ast.parse(py_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if not isinstance(f, ast.Attribute):
                    continue
                if f.attr not in targets:
                    continue
                # Skip the `def` lines (these are FunctionDef, not Call).
                has_ctx = any(kw.arg == "ctx" for kw in node.keywords)
                if not has_ctx:
                    rel = py_path.relative_to(_BACKEND_ROOT)
                    violations.append((str(rel), node.lineno, f.attr))

    assert not violations, (
        f"TE-032 regression: callsite production di tenant-bound "
        f"loader senza `ctx=` kwarg:\n"
        + "\n".join(f"  {v[0]}:{v[1]} → {v[2]}" for v in violations)
        + "\nPost-TE-032 i 2 metodi richiedono ctx esplicito."
    )
