"""
Core-level contract tests for `_carica_sorgenti_progetto` (project_source

cross-tenant isolation).
"""

from unittest.mock import patch

import pytest

from app.core.manipola.executor import ManipolaExecutor
from tests2.helpers.builders import build_canvas, build_node


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


def _make_executor():
    executor = ManipolaExecutor(project_service=object())
    executor.results_base_dir = None
    return executor


def _canvas_with_project_source(source_project_id="proj-99"):
    return build_canvas(
        build_node("project_source", source_project_id=source_project_id, columns=[]),
    )


# Fail-closed quando group_id esplicito e' assente o vuoto


def test_load_project_sources_fail_closed_without_explicit_group_id():
    """Missing group_id kwarg → ctx-strict fail-closed. The loader
    must not fall back to any instance state."""
    executor = _make_executor()
    dataframes = {}

    result = executor._carica_sorgenti_progetto(
        _canvas_with_project_source(), dataframes, force_recalculate=False
    )

    assert result is not None
    assert result["success"] is False
    assert "Contesto di sicurezza" in result["message"]
    assert dataframes == {}


def test_load_project_sources_fail_closed_when_kwarg_is_empty_string():
    """Empty-string kwarg is falsey → ctx-strict fail-closed."""
    executor = _make_executor()
    dataframes = {}

    result = executor._carica_sorgenti_progetto(
        _canvas_with_project_source(), dataframes,
        force_recalculate=False, group_id="",
    )

    assert result is not None
    assert result["success"] is False
    assert "Contesto di sicurezza" in result["message"]


# Cross-tenant blocked esplicito (era gia' coperto, qui ribadiamo come
# baseline post-Refactor #2)


def test_load_project_sources_blocks_cross_tenant_via_resolver():
    """Verifica che il check sia delegato al resolver con
    expected_group_id corretto, non a una logica inline che salta
    sul primo errore."""
    executor = _make_executor()
    dataframes = {}

    from app.core.artifacts import ArtifactResolutionError

    with patch(
        "app.core.artifacts.ArtifactResolver.materialize_latest",
        side_effect=ArtifactResolutionError("project denied"),
    ):
        result = executor._carica_sorgenti_progetto(
            _canvas_with_project_source("proj-99"), dataframes,
            force_recalculate=False,
            group_id="grp-1",
        )

    assert result["success"] is False
    assert "Accesso negato" in result["message"]
    assert "proj-99" in result["message"]
    assert dataframes == {}


# Fail-closed quando il progetto sorgente non esiste affatto


def test_load_project_sources_fail_closed_when_source_project_missing():
    """Pre-Refactor #2 `if _source_row:` con None bypassava il check
    e il flusso proseguiva al load. Ora il resolver solleva
    ArtifactResolutionError (progetto non trovato) e il caller
    fa fail-closed."""
    executor = _make_executor()
    dataframes = {}

    from app.core.artifacts import ArtifactResolutionError

    with patch(
        "app.core.artifacts.ArtifactResolver.materialize_latest",
        side_effect=ArtifactResolutionError("project denied"),
    ):
        result = executor._carica_sorgenti_progetto(
            _canvas_with_project_source("proj-ghost"), dataframes,
            force_recalculate=False,
            group_id="grp-1",
        )

    assert result["success"] is False
    # Il messaggio finale e' uniformato a "Accesso negato" perche'
    # il caller non distingue 'progetto inesistente' da 'gruppo diverso'
    # (entrambi ArtifactResolutionError dal resolver, entrambi fail-closed).
    assert "Accesso negato" in result["message"]


# Fail-closed quando il check esplode con eccezione DB


# Refactor #3 fix CRS: _carica_sorgenti_file riapplica _applica_crs_da_colonne


def test_load_file_sources_reapplies_crs_from_node_resource_columns():
    """Previous behavior dopo Refactor #3 il branch file_source di
    _carica_sorgenti_file non chiamava piu' _applica_crs_da_colonne,
    quindi un file caricato come DataFrame piatto (senza geometria
    detected da geopandas) ma con metadati colonna `geometry:point:4326`
    nel canvas restava DataFrame piatto invece di essere convertito a
    GeoDataFrame con CRS. Le trasformazioni spaziali downstream
    perdevano il CRS."""
    import pandas as pd

    executor = ManipolaExecutor(project_service=object())
    dataframes = {}

    canvas = build_canvas(
        build_node(
            "file_source",
            file_source_id="fs-spatial",
            columns=[
                {"name": "id", "type": "integer"},
                {"name": "geom", "type": "geometry:point:4326"},
            ],
        ),
    )

    # File caricato come DataFrame piatto: il SourceLoader ritorna df
    # (gdf=None), e il CRS deve essere ricostruito qui dal metadata.
    df_flat = pd.DataFrame({
        "id": [1, 2],
        "geom": ["POINT(0 0)", "POINT(1 1)"],
    })
    captured = {}

    def _fake_apply_crs(d, cols, meta=None):
        captured["called"] = True
        captured["columns"] = cols
        captured["meta"] = meta
        return d  # sentinella, non importa il return

    # source_loader e' il consumer reale di is_resource_visible
    with patch("app.core.artifacts.source_loader.is_resource_visible", return_value=True), \
         patch("app.core.files.service.file_source_service") as svc, \
         patch("app.core.manipola.executor._applica_crs_da_colonne",
               side_effect=_fake_apply_crs):
        svc.load_dataframe.return_value = (df_flat, None)
        result = executor._carica_sorgenti_file(
            canvas, dataframes, group_id="grp-1"
        )

    assert result is None  # nessun errore
    assert captured.get("called") is True, (
        "_applica_crs_da_colonne deve essere chiamato sul branch file_source "
        "Current contract (era stato dimenticato durante la migrazione a SourceLoader)"
    )
    # Verifica che le node_columns con metadati geometry siano passate
    assert any(
        c.get("type", "").startswith("geometry") for c in captured["columns"]
    )
    assert captured["meta"] is not None
    assert captured["meta"].geometry_column == "geom"


# TE-007/008/009 — ctx-strict: explicit group_id is the only channel
#
# The `_current_group_id` attribute and `_resolve_executing_group_id` helper
# are gone. All three `_carica_sorgenti_*` methods are ctx-strict: they use
# only the explicit `group_id` kwarg. Tests below lock these invariants.


def test_explicit_group_id_is_forwarded_to_resolver():
    """The explicit `group_id` kwarg is what reaches the resolver's
    `expected_group_id`. There is no fallback: whatever is passed in
    is what determines tenant authorization."""
    executor = _make_executor()
    dataframes = {}

    captured = {}

    from app.core.artifacts import ArtifactResolutionError

    class _StubResolver:
        def materialize_latest(self, ref, *, expected_group_id=None,
                               is_superadmin=False):
            captured["expected_group_id"] = expected_group_id
            # Raise the same typed error the resolver would raise
            # on a cross-tenant block. The production caller catches
            # it and fails closed; the test cares only that the
            # expected_group_id was the one we passed.
            raise ArtifactResolutionError(
                "stub: capture expected_group_id and abort"
            )

    # project_source_runtime fa `from app.core.artifacts import ArtifactResolver`,
    # quindi il binding va patchato sul consumer.
    with patch(
        "app.core.manipola.project_source_runtime.ArtifactResolver",
        return_value=_StubResolver(),
    ), patch(
        "app.core.manipola.project_source_runtime.find_latest_output_file",
        return_value=None,
    ):
        executor._carica_sorgenti_progetto(
            _canvas_with_project_source("proj-99"), dataframes,
            force_recalculate=False,
            group_id="grp-victim",
        )

    assert captured.get("expected_group_id") == "grp-victim", (
        f"Resolver must be called with the EXPLICIT group_id "
        f"('grp-victim'). Got: {captured.get('expected_group_id')!r}. "
        "TE-007/009: explicit kwarg is the only tenant channel."
    )


def test_stale_current_group_id_ignored_when_kwarg_missing():
    """TE-009: no `_current_group_id` attribute exists. Calling without
    the explicit `group_id` kwarg must fail-closed immediately.
    The resolver must not be invoked at all."""
    executor = _make_executor()
    dataframes = {}

    resolver_called = {"count": 0}

    from app.core.artifacts import ArtifactResolutionError  # noqa: F401

    class _StubResolver:
        def materialize_latest(self, ref, *, expected_group_id=None,
                               is_superadmin=False):
            resolver_called["count"] += 1
            raise ArtifactResolutionError("must not reach resolver")

    with patch(
        "app.core.artifacts.ArtifactResolver",
        return_value=_StubResolver(),
    ):
        result = executor._carica_sorgenti_progetto(
            _canvas_with_project_source("proj-99"), dataframes,
            force_recalculate=False,
        )

    assert result is not None
    assert result["success"] is False
    assert "Contesto di sicurezza" in result["message"], (
        f"Expected ctx-strict fail-closed, got: {result['message']!r}. "
        "TE-007: _current_group_id must not authorize project-source "
        "when explicit group_id kwarg is absent."
    )
    assert resolver_called["count"] == 0, (
        "Resolver must NOT be called when group_id kwarg is missing — "
        "ctx-strict fail-closed happens before the resolver."
    )
    assert dataframes == {}


def test_empty_explicit_kwarg_fails_closed_not_fallback():
    """TE-009: an empty-string explicit kwarg is falsey. No fallback
    attribute exists. The project-source chain is ctx-strict: an empty
    kwarg must fail-closed the same way as a missing kwarg."""
    executor = _make_executor()
    dataframes = {}

    resolver_called = {"count": 0}

    from app.core.artifacts import ArtifactResolutionError  # noqa: F401

    class _StubResolver:
        def materialize_latest(self, ref, *, expected_group_id=None,
                               is_superadmin=False):
            resolver_called["count"] += 1
            raise ArtifactResolutionError("must not reach resolver")

    with patch(
        "app.core.artifacts.ArtifactResolver",
        return_value=_StubResolver(),
    ):
        result = executor._carica_sorgenti_progetto(
            _canvas_with_project_source("proj-99"), dataframes,
            force_recalculate=False,
            group_id="",
        )

    assert result is not None
    assert result["success"] is False
    assert "Contesto di sicurezza" in result["message"], (
        f"Expected ctx-strict fail-closed, got: {result['message']!r}. "
        "TE-009: empty-string group_id → fail-closed (no fallback)."
    )
    assert resolver_called["count"] == 0
    assert dataframes == {}


def test_load_project_sources_fail_closed_when_check_raises_unexpectedly():
    """Pre-Refactor #2 il `try/except Exception` swallowava qualsiasi
    errore durante il check degradandolo a warning e proseguiva.
    Ora gli errori del resolver vengono lasciati propagare al caller
    (manipola.execute), che li traduce in failure dello step."""
    executor = _make_executor()
    dataframes = {}

    def _boom(*_a, **_k):
        raise RuntimeError("DB esplode")

    with patch(
        "app.core.artifacts.ArtifactResolver.materialize_latest",
        side_effect=_boom,
    ):
        # Qualsiasi RuntimeError dal resolver risale al caller; manipola
        # NON lo deve degradare a warning. Il test verifica
        # che NON arriviamo a un return success-style: o fail-closed o
        # eccezione propagata.
        try:
            result = executor._carica_sorgenti_progetto(
                _canvas_with_project_source("proj-99"), dataframes,
                force_recalculate=False,
            )
            # Se non solleva, deve essere fail dict
            assert result is not None
            assert result.get("success") is False
        except RuntimeError:
            # Anche eccezione propagata e' fail-closed accettabile:
            # l'importante e' che NON sia silenziato con warning.
            pass

    # In ogni caso il dataframe NON deve essere stato caricato.
    assert dataframes == {}
