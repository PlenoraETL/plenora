"""Core-level contract test per Refactor #2 invariante A
(project_source / cross-tenant in manipola).

Pre-Refactor #2 il check cross-tenant in
`manipola.executor._carica_sorgenti_progetto` aveva tre fail-OPEN:

1. `if _executing_project_group:` — se il group_id corrente non era
   determinabile, il check veniva SALTATO interamente.
2. `if _source_row:` — se `get_project(source_project_id)` ritornava
   None (errore DB / progetto inesistente), il check era saltato.
3. `except Exception` → `logger.warning("Cross-tenant check fallito
   (degradato a warning)")` + continue: qualsiasi errore durante il check
   veniva swallowato e l'esecuzione proseguiva.

In piu', il successivo `materialize_latest(...)` veniva chiamato
SENZA `expected_group_id`, quindi anche il check del resolver era
disabilitato — fail-OPEN totale.

Post-Refactor #2 il check e' delegato a
`ArtifactResolver.materialize_latest(..., expected_group_id=...)`
con `expected_group_id` derivato da `self._current_group_id` (che
`manipola.execute()` garantisce non-None). Tutti e tre i path di
errore producono fail-closed esplicito.

Questi test girano DIRETTAMENTE su `_carica_sorgenti_progetto` —
nessun TestClient HTTP, nessun CurrentUser.
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


def _make_executor(group_id):
    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = group_id
    executor.results_base_dir = None
    return executor


def _canvas_with_project_source(source_project_id="proj-99"):
    return build_canvas(
        build_node("project_source", source_project_id=source_project_id, columns=[]),
    )


# ---------------------------------------------------------------------------
# Fail-closed quando _current_group_id non e' determinabile
# ---------------------------------------------------------------------------


def test_load_project_sources_fail_closed_when_current_group_is_none():
    """Pre-Refactor #2 il check era `if _executing_project_group:` →
    None bypassava interamente. Ora hard fail."""
    executor = _make_executor(group_id=None)
    dataframes = {}

    result = executor._carica_sorgenti_progetto(
        _canvas_with_project_source(), dataframes, force_recalculate=False
    )

    assert result is not None
    assert result["success"] is False
    assert "Contesto di sicurezza" in result["message"]
    assert dataframes == {}


def test_load_project_sources_fail_closed_when_current_group_is_empty_string():
    executor = _make_executor(group_id="")
    dataframes = {}

    result = executor._carica_sorgenti_progetto(
        _canvas_with_project_source(), dataframes, force_recalculate=False
    )

    assert result is not None
    assert result["success"] is False
    assert "Contesto di sicurezza" in result["message"]


# ---------------------------------------------------------------------------
# Cross-tenant blocked esplicito (era gia' coperto, qui ribadiamo come
# baseline post-Refactor #2)
# ---------------------------------------------------------------------------


def test_load_project_sources_blocks_cross_tenant_via_resolver():
    """Verifica che il check sia delegato al resolver con
    expected_group_id corretto, non a una logica inline che salta
    sul primo errore."""
    executor = _make_executor(group_id="grp-1")
    dataframes = {}

    with patch("app.db.db_service.get_project",
               return_value={"id": "proj-99", "group_id": "grp-2"}):
        result = executor._carica_sorgenti_progetto(
            _canvas_with_project_source("proj-99"), dataframes,
            force_recalculate=False,
        )

    assert result["success"] is False
    assert "Accesso negato" in result["message"]
    assert "proj-99" in result["message"]
    assert dataframes == {}


# ---------------------------------------------------------------------------
# Fail-closed quando il progetto sorgente non esiste affatto
# ---------------------------------------------------------------------------


def test_load_project_sources_fail_closed_when_source_project_missing():
    """Pre-Refactor #2 `if _source_row:` con None bypassava il check
    e il flusso proseguiva al load. Ora il resolver solleva
    ArtifactResolutionError (progetto non trovato) e il caller
    fa fail-closed."""
    executor = _make_executor(group_id="grp-1")
    dataframes = {}

    with patch("app.db.db_service.get_project", return_value=None):
        result = executor._carica_sorgenti_progetto(
            _canvas_with_project_source("proj-ghost"), dataframes,
            force_recalculate=False,
        )

    assert result["success"] is False
    # Il messaggio finale e' uniformato a "Accesso negato" perche'
    # il caller non distingue 'progetto inesistente' da 'gruppo diverso'
    # (entrambi ArtifactResolutionError dal resolver, entrambi fail-closed).
    assert "Accesso negato" in result["message"]


# ---------------------------------------------------------------------------
# Fail-closed quando il check esplode con eccezione DB
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Refactor #3 fix CRS: _carica_sorgenti_file riapplica _applica_crs_da_colonne
# ---------------------------------------------------------------------------


def test_load_file_sources_reapplies_crs_from_node_columns_metadata():
    """Pre-fix dopo Refactor #3 il branch file_source di
    _carica_sorgenti_file non chiamava piu' _applica_crs_da_colonne,
    quindi un file caricato come DataFrame piatto (senza geometria
    detected da geopandas) ma con metadati colonna `geometry:point:4326`
    nel canvas restava DataFrame piatto invece di essere convertito a
    GeoDataFrame con CRS. Le trasformazioni spaziali downstream
    perdevano il CRS."""
    import pandas as pd

    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = "grp-1"
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

    def _fake_apply_crs(d, cols):
        captured["called"] = True
        captured["columns"] = cols
        return d  # sentinella, non importa il return

    with patch("app.shared.sharing.is_resource_visible", return_value=True), \
         patch("app.services.file_service.file_source_service") as svc, \
         patch("app.core.manipola.executor._applica_crs_da_colonne",
               side_effect=_fake_apply_crs):
        svc.load_dataframe.return_value = (df_flat, None)
        result = executor._carica_sorgenti_file(canvas, dataframes)

    assert result is None  # nessun errore
    assert captured.get("called") is True, (
        "_applica_crs_da_colonne deve essere chiamato sul branch file_source "
        "post-fix (era stato dimenticato durante la migrazione a SourceLoader)"
    )
    # Verifica che le node_columns con metadati geometry siano passate
    assert any(
        c.get("type", "").startswith("geometry") for c in captured["columns"]
    )


def test_load_project_sources_fail_closed_when_check_raises_unexpectedly():
    """Pre-Refactor #2 il `try/except Exception` swallowava qualsiasi
    errore durante il check degradandolo a warning e proseguiva.
    Ora gli errori del resolver vengono lasciati propagare al caller
    (manipola.execute), che li traduce in failure dello step."""
    executor = _make_executor(group_id="grp-1")
    dataframes = {}

    def _boom(*_a, **_k):
        raise RuntimeError("DB esplode")

    with patch("app.db.db_service.get_project", side_effect=_boom):
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
