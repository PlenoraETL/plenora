"""
Core-level test post-Step B Refactor #3.

Verifica che `api_connector._load_source_project` e `_load_file_source`
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.arricchisci.api_connector import ApiConnectionService
from app.core.arricchisci.execution_context import ArricchisciExecutionContext
from app.core.artifacts import (
    LatestProjectArtifactRef,
    MaterializedArtifact,
    SourceLoadingError,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


def _make_service(group_id="grp-1"):
    svc = ApiConnectionService.__new__(ApiConnectionService)
    svc._current_group_id = group_id
    svc.results_dir = None
    return svc


def _make_service_no_singleton():
    """BE-GO-READY-TRANSFORMATION-ENGINE-028: fixture per il subset
    `_load_file_source` migrato su ctx esplicito. Costruisce un
    `ApiConnectionService` SENZA pre-set di `_current_group_id` —
    il caller deve passare `ctx=ArricchisciExecutionContext(...)`
    per la tenant binding."""
    svc = ApiConnectionService.__new__(ApiConnectionService)
    svc.results_dir = None
    return svc


def _ctx_grp(group_id: str | None = "grp-1",
             project_id: str = "p-1") -> ArricchisciExecutionContext:
    return ArricchisciExecutionContext(
        project_id=project_id,
        group_id=group_id,
        preview_limit=None,
    )


# FIXME: 1 chiuso: _load_source_project propaga expected_group_id al resolver


def test_load_source_project_propagates_group_id_via_source_loader():
    """Verifica end-to-end: il wrapper costruisce un SourceRef e
    chiama SourceLoader.load con expected_group_id non vuoto. Il
    resolver riceve il group_id e applica il check cross-tenant.
    Pre-Refactor #3: materialize_latest era chiamato senza
    expected_group_id → check disabilitato.

    BE-GO-READY-TRANSFORMATION-ENGINE-029: migrato a ctx esplicito.
    Stale singleton "stale-grp-LEAK" prova che `ctx.group_id` vince
    sul singleton legacy."""
    svc = _make_service_no_singleton()
    svc._current_group_id = "stale-grp-LEAK"
    captured = {}

    def _fake_load(ref, *, expected_group_id):
        captured["ref"] = ref
        captured["group_id"] = expected_group_id
        return pd.DataFrame({"x": [1]})

    with patch("app.core.artifacts.SourceLoader") as loader_cls:
        loader_cls.return_value.load.side_effect = _fake_load
        df = svc._load_source_project(
            {"source_project_id": "proj-1", "columns": []},
            force_recalculate=False,
            ctx=_ctx_grp(group_id="grp-A"),
        )

    # Ctx wins over stale singleton.
    assert captured["group_id"] == "grp-A"
    assert isinstance(captured["ref"].project, LatestProjectArtifactRef)
    assert captured["ref"].project.project_id == "proj-1"
    assert df["x"].tolist() == [1]


def test_load_source_project_fail_closed_without_ctx_group_id():
    """BE-GO-READY-TRANSFORMATION-ENGINE-030B: ctx-strict fail-closed.

    Pre-TE-030B il loader leggeva il group_id via `_resolve_group_id`
    (legacy ctx-or-singleton) e questo test verificava il fail-closed
    sul fallback singleton (`_make_service(group_id=None)` + no ctx).
    Post-TE-030B `_load_source_project` usa `_group_id_from_ctx`
    strict: il fail-closed e' triggerato da `ctx.group_id=None`,
    NON dal singleton.

    Stale singleton 'stale-grp-LEAK' confirma che il singleton non
    e' piu' consultato (anche con un valore non-None il fail-closed
    scatta perche' `ctx.group_id=None`).
    """
    svc = _make_service_no_singleton()
    svc._current_group_id = "stale-grp-LEAK"
    with pytest.raises(ValueError, match="Contesto di sicurezza"):
        svc._load_source_project(
            {"source_project_id": "proj-1"},
            ctx=_ctx_grp(group_id=None),
        )


def test_load_source_project_blocks_cross_tenant_via_loader():
    """End-to-end con resolver reale: cross-tenant → ValueError
    con il messaggio storico (back-compat con i test che lo
    asseriscono).

    BE-GO-READY-TRANSFORMATION-ENGINE-029: migrato a ctx esplicito.
    Stale singleton prova che la cross-tenant block usa
    `ctx.group_id`, non il singleton legacy."""
    import app.db.config.projects as projects_db
    from app.core.results import db as results_db

    svc = _make_service_no_singleton()
    svc._current_group_id = "stale-grp-LEAK"
    with patch.object(projects_db, "get_project", return_value=None), \
         patch.object(results_db, "get_latest_result", return_value=None):
        with pytest.raises(ValueError, match="Accesso negato: progetto sorgente 'proj-99'"):
            svc._load_source_project(
                {"source_project_id": "proj-99"},
                ctx=_ctx_grp(group_id="grp-mine"),
            )


def test_load_source_project_does_not_leak_cross_tenant_detail_in_message():
    """Pre-fix il wrapper mappava OGNI SourceLoadingError a
    'appartiene a un gruppo diverso' anche quando in realta' era
    'progetto inesistente' o 'errore DB'. Ora il resolver usa lookup
    tenant-scoped: cross-tenant e progetto assente hanno lo stesso
    dettaglio non-enumerabile.

    BE-GO-READY-TRANSFORMATION-ENGINE-029: migrato a ctx esplicito.
    Stale singleton prova che il check usa `ctx.group_id`, non il
    singleton legacy."""
    import app.db.config.projects as projects_db
    from app.core.results import db as results_db

    svc = _make_service_no_singleton()
    svc._current_group_id = "stale-grp-LEAK"
    with patch.object(projects_db, "get_project", return_value=None), \
         patch.object(results_db, "get_latest_result", return_value=None):
        with pytest.raises(ValueError) as exc_info:
            svc._load_source_project(
                {"source_project_id": "proj-99"},
                ctx=_ctx_grp(group_id="grp-mine"),
            )

    msg = str(exc_info.value)
    # Prefisso storico per back-compat
    assert "Accesso negato: progetto sorgente 'proj-99'" in msg
    # Dettaglio anti-enumeration: niente reveal del gruppo reale.
    assert "non trovato" in msg
    assert "Cross-tenant" not in msg
    assert "grp-other" not in msg


def test_load_source_project_distinguishes_project_not_found_from_cross_tenant():
    """Caso diverso da cross-tenant: progetto inesistente. Il
    messaggio deve riportare 'non trovato', non 'gruppo diverso'.

    BE-GO-READY-TRANSFORMATION-ENGINE-029: migrato a ctx esplicito.
    Stale singleton presente come decoy."""
    import app.db.config.projects as projects_db

    svc = _make_service_no_singleton()
    svc._current_group_id = "stale-grp-LEAK"
    with patch.object(projects_db, "get_project", return_value=None):
        with pytest.raises(ValueError) as exc_info:
            svc._load_source_project(
                {"source_project_id": "proj-ghost"},
                ctx=_ctx_grp(group_id="grp-mine"),
            )

    msg = str(exc_info.value)
    assert "Accesso negato: progetto sorgente 'proj-ghost'" in msg
    assert "non trovato" in msg
    # NON deve dire "gruppo diverso" perche' non lo e'
    assert "gruppo diverso" not in msg


# Fix #2: _resolve_file_source_path branch project_source con expected_group_id


def test_resolve_file_source_path_project_branch_passes_through_source_loader():
    """Post-fix #boundary, _resolve_file_source_path NON tocca piu'
    ArtifactResolver direttamente: passa per
    SourceLoader.resolve_project_path che a sua volta delega al
    resolver con expected_group_id propagato.

    Verifichiamo che (a) il loader venga chiamato, (b) con il
    group_id corrente, (c) col project_id estratto dal node.

    BE-GO-READY-TRANSFORMATION-ENGINE-029: migrato a ctx esplicito.
    Il branch project_source di `_resolve_file_source_path` usa
    `_extract_project_source_group_id` (TE-026 strict) che legge
    SOLO da `ctx`. Stale singleton confirma che il singleton legacy
    non viene consultato."""
    svc = _make_service_no_singleton()
    svc._current_group_id = "stale-grp-LEAK"
    nodes = [
        {"id": "src-1", "type": "project_source", "data": {"source_project_id": "proj-1"}},
        {"id": "tgt-1"},
    ]
    edges = [{"source": "src-1", "target": "tgt-1"}]

    with patch("app.core.artifacts.SourceLoader") as loader_cls:
        loader_cls.return_value.resolve_project_path.return_value = "/tmp/output.csv"
        result = svc._resolve_file_source_path(
            nodes, edges, "tgt-1",
            ctx=_ctx_grp(group_id="grp-A"),
        )

    assert result == "/tmp/output.csv"
    loader_cls.return_value.resolve_project_path.assert_called_once()
    call_args = loader_cls.return_value.resolve_project_path.call_args
    # Argomento posizionale: LatestProjectArtifactRef(project_id="proj-1")
    project_ref = call_args.args[0]
    assert project_ref.project_id == "proj-1"
    # Keyword: expected_group_id propagato — ctx.group_id, NON stale singleton
    assert call_args.kwargs.get("expected_group_id") == "grp-A"


def test_resolve_file_source_path_blocks_cross_tenant_project_source():
    """End-to-end: project_source di altro gruppo → ValueError
    fail-closed. Pre-fix questo path ritornava silenziosamente None
    o tentava di servire il file di un altro tenant.

    BE-GO-READY-TRANSFORMATION-ENGINE-029: migrato a ctx esplicito.
    Stale singleton prova che il check cross-tenant usa
    `ctx.group_id`, non il singleton legacy."""
    import app.db.config.projects as projects_db
    from app.core.results import db as results_db

    svc = _make_service_no_singleton()
    svc._current_group_id = "stale-grp-LEAK"
    nodes = [
        {"id": "src-1", "type": "project_source", "data": {"source_project_id": "proj-other"}},
        {"id": "tgt-1"},
    ]
    edges = [{"source": "src-1", "target": "tgt-1"}]

    with patch.object(projects_db, "get_project", return_value=None), \
         patch.object(results_db, "get_latest_result", return_value=None):
        with pytest.raises(ValueError, match="Accesso negato: progetto sorgente 'proj-other'"):
            svc._resolve_file_source_path(
                nodes, edges, "tgt-1",
                ctx=_ctx_grp(group_id="grp-mine"),
            )


def test_resolve_file_source_path_fail_closed_without_ctx_group_id():
    """BE-GO-READY-TRANSFORMATION-ENGINE-030B: ctx-strict fail-closed
    per il branch project_source di `_resolve_file_source_path` (gia'
    ctx-strict via `_extract_project_source_group_id` da TE-026).

    Adottato il canonical pattern stale-singleton-LEAK + ctx con
    group_id=None per allinearsi ai 3 critical fail-closed locks
    sotto la nuova semantica TE-030B.
    """
    svc = _make_service_no_singleton()
    svc._current_group_id = "stale-grp-LEAK"
    nodes = [
        {"id": "src-1", "type": "project_source", "data": {"source_project_id": "proj-1"}},
        {"id": "tgt-1"},
    ]
    edges = [{"source": "src-1", "target": "tgt-1"}]

    with pytest.raises(ValueError, match="Contesto di sicurezza"):
        svc._resolve_file_source_path(
            nodes, edges, "tgt-1", ctx=_ctx_grp(group_id=None),
        )


# FIXME: 2 chiuso: _load_file_source preserva il GeoDataFrame


def test_load_file_source_preserves_geodataframe():
    """Pre-Refactor #3 questo metodo faceva `df, _ = ...` scartando
    il gdf. Ora il loader unico ritorna `gdf if gdf is not None else df`,
    preservando il CRS per le trasformazioni spaziali downstream.

    BE-GO-READY-TRANSFORMATION-ENGINE-028: migrato a ctx esplicito.
    Il singleton `_current_group_id` e' impostato a "stale-grp-LEAK"
    per provare che `ctx.group_id` vince. Senza migrazione,
    questo test non avrebbe coverage del path Phase-2 ctx (solo
    del fallback Phase-1 singleton)."""
    svc = _make_service_no_singleton()
    # Stale singleton: NON deve essere consultato perche' passiamo ctx.
    svc._current_group_id = "stale-grp-LEAK"
    df = pd.DataFrame({"id": [1]})
    gdf = MagicMock(name="GeoDataFrame")
    gdf.columns = ["id"]
    gdf.__getitem__ = lambda self, key: pd.Series([1])

    with patch("app.shared.sharing.is_resource_visible", return_value=True), \
         patch("app.core.files.service.file_source_service") as svc_fs, \
         patch("app.utils.dataframe.filter_visible_columns",
               side_effect=lambda d, _cols: d):
        svc_fs.load_dataframe.return_value = (df, gdf)
        out = svc._load_file_source(
            {"file_source_id": "fs-spatial", "columns": []},
            ctx=_ctx_grp(group_id="grp-1"),
        )

    # Il loader deve aver restituito gdf (non df)
    assert out is gdf
    # Locale: il loader e' stato chiamato con il group_id da ctx,
    # non con il singleton stale.
    assert svc_fs.load_dataframe.call_args is not None
    # `load_dataframe(file_source_id, expected_group_id="grp-1")`.
    call_kwargs = svc_fs.load_dataframe.call_args.kwargs
    call_args = svc_fs.load_dataframe.call_args.args
    seen_group = call_kwargs.get("expected_group_id")
    if seen_group is None:
        # Alcuni test fixture passano group_id come positional; cerca nel tuple.
        seen_group = next((a for a in call_args if a == "grp-1"), None)
    assert seen_group == "grp-1", (
        f"Expected loader chiamato con expected_group_id=grp-1; "
        f"call_args={call_args} call_kwargs={call_kwargs}"
    )


def test_load_file_source_falls_back_to_df_when_no_gdf():
    """Sanity: file non geografico → ritorna df piatto, niente
    regressioni rispetto al comportamento legacy.

    BE-GO-READY-TRANSFORMATION-ENGINE-028: migrato a ctx esplicito.
    Stale singleton per provare che ctx vince."""
    svc = _make_service_no_singleton()
    svc._current_group_id = "stale-grp-LEAK"
    df = pd.DataFrame({"id": [1, 2]})

    with patch("app.shared.sharing.is_resource_visible", return_value=True), \
         patch("app.core.files.service.file_source_service") as svc_fs, \
         patch("app.utils.dataframe.filter_visible_columns",
               side_effect=lambda d, _cols: d):
        svc_fs.load_dataframe.return_value = (df, None)
        out = svc._load_file_source(
            {"file_source_id": "fs-flat", "columns": []},
            ctx=_ctx_grp(group_id="grp-1"),
        )

    assert out is df


def test_load_file_source_blocks_cross_tenant_via_loader():
    """Visibility check enforced dal loader: file non visibile →
    ValueError dal wrapper (tradotto da SourceLoadingError).

    BE-GO-READY-TRANSFORMATION-ENGINE-028: migrato a ctx esplicito.
    Stale singleton per provare che ctx vince — anche se il
    singleton avesse un group "permissivo", la visibility check
    e' chiamata con `ctx.group_id`."""
    svc = _make_service_no_singleton()
    svc._current_group_id = "stale-grp-LEAK"

    with patch("app.shared.sharing.is_resource_visible", return_value=False), \
         patch("app.core.files.service.file_source_service") as svc_fs:
        with pytest.raises(ValueError, match="non appartiene al tuo gruppo"):
            svc._load_file_source(
                {"file_source_id": "fs-99"},
                ctx=_ctx_grp(group_id="grp-1"),
            )

    # Il loader NON deve aver tentato il load_dataframe
    svc_fs.load_dataframe.assert_not_called()


def test_load_file_source_fail_closed_without_ctx_group_id():
    """BE-GO-READY-TRANSFORMATION-ENGINE-030B: ctx-strict fail-closed
    su `_load_file_source` (post-promozione a `_group_id_from_ctx`).

    Stale singleton 'stale-grp-LEAK' confirma che il singleton non
    viene piu' consultato dal nuovo helper strict.
    """
    svc = _make_service_no_singleton()
    svc._current_group_id = "stale-grp-LEAK"
    with pytest.raises(ValueError, match="Contesto di sicurezza"):
        svc._load_file_source(
            {"file_source_id": "fs-1"},
            ctx=_ctx_grp(group_id=None),
        )


# Static values: nessuna regressione


def test_load_static_values_works_without_db_or_user():
    """Static branch puro: niente DB, niente HTTP, niente group_id
    semanticamente significativo."""
    svc = _make_service(group_id="grp-1")
    df = svc._load_static_values({
        "fields": [
            {"name": "city", "value": "Roma"},
            {"name": "code", "value": "42"},
        ]
    })
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["city"] == "Roma"


def test_load_static_values_empty_fields_returns_single_empty_row():
    svc = _make_service(group_id="grp-1")
    df = svc._load_static_values({"fields": []})
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1


# BE-GO-READY-TRANSFORMATION-ENGINE-028: classification lock


def test_te030b_critical_fail_closed_locks_use_ctx_strict_missing_group():
    """Source-bound guard: i 3 test critical fail-closed lock,
    migrati in TE-030B da pattern legacy singleton-fallback a pattern
    ctx-strict missing group, DEVONO mantenere la canonical shape:
      1. `_make_service_no_singleton()` (NON `_make_service(...)`);
      2. setattr `_current_group_id = "stale-grp-LEAK"` (decoy);
      3. `ctx=_ctx_grp(group_id=None)` al call site.

    Pre-TE-030B (TE-028) la shape era inversa: `_make_service(
    group_id=None)` + no ctx — esercitava la legacy fallback. Post-
    TE-030B il helper legacy `_resolve_group_id` e' rimosso e i 2
    callsite production usano `_group_id_from_ctx` strict; la sola
    via di fail-closed e' un ctx con `group_id=None`.

    I 3 test critical (rinominati da TE-028 a TE-030B):
      - `test_load_source_project_fail_closed_without_ctx_group_id`
      - `test_resolve_file_source_path_fail_closed_without_ctx_group_id`
      - `test_load_file_source_fail_closed_without_ctx_group_id`
    """
    import ast
    from pathlib import Path

    text = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)

    critical_tests = {
        "test_load_source_project_fail_closed_without_ctx_group_id",
        "test_resolve_file_source_path_fail_closed_without_ctx_group_id",
        "test_load_file_source_fail_closed_without_ctx_group_id",
    }

    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in critical_tests:
            body_src = ast.unparse(node)
            found[node.name] = body_src

    missing = critical_tests - set(found.keys())
    assert not missing, (
        f"TE-030B critical fail-closed locks missing in this file:\n"
        f"  {sorted(missing)}\n"
        f"These tests prove the ctx-strict fail-closed path: when "
        f"`ctx.group_id=None`, the loader rifiuta esplicitamente. "
        f"Removing them weakens the security contract."
    )

    for test_name, body_src in found.items():
        # 1. Must use `_make_service_no_singleton()` (NOT
        #    `_make_service(group_id=...)`).
        assert "_make_service_no_singleton()" in body_src, (
            f"{test_name}: must construct svc via "
            f"`_make_service_no_singleton()` — TE-030B migration moves "
            f"the fail-closed assertion off the singleton path. "
            f"Got body:\n{body_src}"
        )
        # 2. Must set stale singleton decoy.
        assert (
            'svc._current_group_id = "stale-grp-LEAK"' in body_src
            or "svc._current_group_id = 'stale-grp-LEAK'" in body_src
        ), (
            f"{test_name}: must set svc._current_group_id = "
            f"'stale-grp-LEAK' to prove the singleton is NOT consulted "
            f"by the strict helper. Got body:\n{body_src}"
        )
        # 3. Must pass `ctx=_ctx_grp(group_id=None)`.
        assert "ctx=_ctx_grp(group_id=None)" in body_src, (
            f"{test_name}: must pass `ctx=_ctx_grp(group_id=None)` to "
            f"the SUT call — this is what triggers the ctx-strict "
            f"fail-closed path. Got body:\n{body_src}"
        )


def test_te028_migrated_load_file_source_tests_use_ctx_and_stale_singleton():
    """Source-bound guard: i 3 test `_load_file_source` migrati a ctx
    da TE-028 DEVONO continuare a passare `ctx=...` E settare
    `_current_group_id` a uno "stale leak" diverso dal valore di ctx.
    Questo prova che ctx vince sul singleton, non solo che ctx
    funziona quando il singleton non e' impostato."""
    import ast
    from pathlib import Path

    text = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)

    migrated_tests = {
        "test_load_file_source_preserves_geodataframe",
        "test_load_file_source_falls_back_to_df_when_no_gdf",
        "test_load_file_source_blocks_cross_tenant_via_loader",
    }

    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in migrated_tests:
            body_src = ast.unparse(node)
            found[node.name] = body_src

    missing = migrated_tests - set(found.keys())
    assert not missing, (
        f"TE-028 migrated tests missing: {sorted(missing)}"
    )

    for test_name, body_src in found.items():
        assert "_make_service_no_singleton()" in body_src, (
            f"{test_name}: must use `_make_service_no_singleton()` "
            f"fixture — TE-028 migration moves these tests off the "
            f"singleton-pre-set pattern. Got body:\n{body_src}"
        )
        # `ast.unparse` normalizes string quoting, so check both forms.
        assert (
            'svc._current_group_id = "stale-grp-LEAK"' in body_src
            or "svc._current_group_id = 'stale-grp-LEAK'" in body_src
        ), (
            f"{test_name}: must set svc._current_group_id = "
            f"'stale-grp-LEAK' to prove ctx wins over stale singleton. "
            f"Got body:\n{body_src}"
        )
        assert "ctx=_ctx_grp(" in body_src, (
            f"{test_name}: must pass `ctx=_ctx_grp(group_id=\"grp-1\")` "
            f"to the SUT call. Got body:\n{body_src}"
        )
