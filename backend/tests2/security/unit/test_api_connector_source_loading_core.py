"""Core-level test post-Step B Refactor #3.

Verifica che `api_connector._load_source_project` e `_load_file_source`
ora delegano a `SourceLoader`, chiudendo i due bug latenti rilevati
nell'audit:

**Bug 1 (security)** — `_load_source_project` chiamava
`materialize_latest(...)` SENZA `expected_group_id`, disabilitando
il check cross-tenant del resolver. Stesso fail-OPEN che era stato
chiuso in manipola al review precedente.

**Bug 2 (correctness)** — `_load_file_source` faceva
`df, _ = file_source_service.load_dataframe(...)`, scartando il
GeoDataFrame e perdendo il CRS lungo le trasformazioni downstream.

Test diretti su `ApiConnectionService._load_*`, niente HTTP, niente
CurrentUser.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.arricchisci.api_connector import ApiConnectionService
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


# ---------------------------------------------------------------------------
# Bug 1 chiuso: _load_source_project propaga expected_group_id al resolver
# ---------------------------------------------------------------------------


def test_load_source_project_propagates_group_id_via_source_loader():
    """Verifica end-to-end: il wrapper costruisce un SourceRef e
    chiama SourceLoader.load con expected_group_id non vuoto. Il
    resolver riceve il group_id e applica il check cross-tenant.
    Pre-Refactor #3: materialize_latest era chiamato senza
    expected_group_id → check disabilitato."""
    svc = _make_service(group_id="grp-A")
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
        )

    assert captured["group_id"] == "grp-A"
    assert isinstance(captured["ref"].project, LatestProjectArtifactRef)
    assert captured["ref"].project.project_id == "proj-1"
    assert df["x"].tolist() == [1]


def test_load_source_project_fail_closed_without_current_group_id():
    """Senza _current_group_id non si carica niente: hard fail
    (era fail-OPEN parziale pre-Refactor #3)."""
    svc = _make_service(group_id=None)
    with pytest.raises(ValueError, match="Contesto di sicurezza"):
        svc._load_source_project({"source_project_id": "proj-1"})


def test_load_source_project_blocks_cross_tenant_via_loader():
    """End-to-end con resolver reale: cross-tenant → ValueError
    con il messaggio storico (back-compat con i test che lo
    asseriscono)."""
    import app.db.db_service as db_service

    svc = _make_service(group_id="grp-mine")
    with patch.object(db_service, "get_project",
                      return_value={"id": "proj-99", "group_id": "grp-other"}), \
         patch.object(db_service, "get_latest_result", return_value=None):
        with pytest.raises(ValueError, match="Accesso negato: progetto sorgente 'proj-99'"):
            svc._load_source_project({"source_project_id": "proj-99"})


def test_load_source_project_preserves_cross_tenant_detail_in_message():
    """Pre-fix il wrapper mappava OGNI SourceLoadingError a
    'appartiene a un gruppo diverso' anche quando in realta' era
    'progetto inesistente' o 'errore DB'. Ora il messaggio del
    loader viene preservato dopo il prefisso storico."""
    import app.db.db_service as db_service

    svc = _make_service(group_id="grp-mine")
    with patch.object(db_service, "get_project",
                      return_value={"id": "proj-99", "group_id": "grp-other"}), \
         patch.object(db_service, "get_latest_result", return_value=None):
        with pytest.raises(ValueError) as exc_info:
            svc._load_source_project({"source_project_id": "proj-99"})

    msg = str(exc_info.value)
    # Prefisso storico per back-compat
    assert "Accesso negato: progetto sorgente 'proj-99'" in msg
    # Dettaglio preservato dal SourceLoadingError originale
    assert "Cross-tenant" in msg
    assert "grp-other" in msg


def test_load_source_project_distinguishes_project_not_found_from_cross_tenant():
    """Caso diverso da cross-tenant: progetto inesistente. Il
    messaggio deve riportare 'non trovato', non 'gruppo diverso'."""
    import app.db.db_service as db_service

    svc = _make_service(group_id="grp-mine")
    with patch.object(db_service, "get_project", return_value=None):
        with pytest.raises(ValueError) as exc_info:
            svc._load_source_project({"source_project_id": "proj-ghost"})

    msg = str(exc_info.value)
    assert "Accesso negato: progetto sorgente 'proj-ghost'" in msg
    assert "non trovato" in msg
    # NON deve dire "gruppo diverso" perche' non lo e'
    assert "gruppo diverso" not in msg


# ---------------------------------------------------------------------------
# Fix #2: _resolve_file_source_path branch project_source con expected_group_id
# ---------------------------------------------------------------------------


def test_resolve_file_source_path_project_branch_passes_through_source_loader():
    """Post-fix #boundary, _resolve_file_source_path NON tocca piu'
    ArtifactResolver direttamente: passa per
    SourceLoader.resolve_project_path che a sua volta delega al
    resolver con expected_group_id propagato.

    Verifichiamo che (a) il loader venga chiamato, (b) con il
    group_id corrente, (c) col project_id estratto dal node."""
    svc = _make_service(group_id="grp-A")
    nodes = [
        {"id": "src-1", "type": "project_source", "data": {"source_project_id": "proj-1"}},
        {"id": "tgt-1"},
    ]
    edges = [{"source": "src-1", "target": "tgt-1"}]

    with patch("app.core.artifacts.SourceLoader") as loader_cls:
        loader_cls.return_value.resolve_project_path.return_value = "/tmp/output.csv"
        result = svc._resolve_file_source_path(nodes, edges, "tgt-1")

    assert result == "/tmp/output.csv"
    loader_cls.return_value.resolve_project_path.assert_called_once()
    call_args = loader_cls.return_value.resolve_project_path.call_args
    # Argomento posizionale: LatestProjectArtifactRef(project_id="proj-1")
    project_ref = call_args.args[0]
    assert project_ref.project_id == "proj-1"
    # Keyword: expected_group_id propagato
    assert call_args.kwargs.get("expected_group_id") == "grp-A"


def test_resolve_file_source_path_blocks_cross_tenant_project_source():
    """End-to-end: project_source di altro gruppo → ValueError
    fail-closed. Pre-fix questo path ritornava silenziosamente None
    o tentava di servire il file di un altro tenant."""
    import app.db.db_service as db_service

    svc = _make_service(group_id="grp-mine")
    nodes = [
        {"id": "src-1", "type": "project_source", "data": {"source_project_id": "proj-other"}},
        {"id": "tgt-1"},
    ]
    edges = [{"source": "src-1", "target": "tgt-1"}]

    with patch.object(db_service, "get_project",
                      return_value={"id": "proj-other", "group_id": "grp-other"}), \
         patch.object(db_service, "get_latest_result", return_value=None):
        with pytest.raises(ValueError, match="Accesso negato: progetto sorgente 'proj-other'"):
            svc._resolve_file_source_path(nodes, edges, "tgt-1")


def test_resolve_file_source_path_fail_closed_without_current_group_id():
    svc = _make_service(group_id=None)
    nodes = [
        {"id": "src-1", "type": "project_source", "data": {"source_project_id": "proj-1"}},
        {"id": "tgt-1"},
    ]
    edges = [{"source": "src-1", "target": "tgt-1"}]

    with pytest.raises(ValueError, match="Contesto di sicurezza"):
        svc._resolve_file_source_path(nodes, edges, "tgt-1")


# ---------------------------------------------------------------------------
# Bug 2 chiuso: _load_file_source preserva il GeoDataFrame
# ---------------------------------------------------------------------------


def test_load_file_source_preserves_geodataframe():
    """Pre-Refactor #3 questo metodo faceva `df, _ = ...` scartando
    il gdf. Ora il loader unico ritorna `gdf if gdf is not None else df`,
    preservando il CRS per le trasformazioni spaziali downstream."""
    svc = _make_service(group_id="grp-1")
    df = pd.DataFrame({"id": [1]})
    gdf = MagicMock(name="GeoDataFrame")
    # Il MagicMock deve passare per filter_visible_columns: lo facciamo
    # comportare come un DataFrame con .columns e __getitem__ semplici.
    gdf.columns = ["id"]
    gdf.__getitem__ = lambda self, key: pd.Series([1])

    with patch("app.shared.sharing.is_resource_visible", return_value=True), \
         patch("app.services.file_service.file_source_service") as svc_fs, \
         patch("app.core.arricchisci.api_connector.filter_visible_columns",
               side_effect=lambda d, _cols: d):
        svc_fs.load_dataframe.return_value = (df, gdf)
        out = svc._load_file_source({"file_source_id": "fs-spatial", "columns": []})

    # Il loader deve aver restituito gdf (non df)
    assert out is gdf


def test_load_file_source_falls_back_to_df_when_no_gdf():
    """Sanity: file non geografico → ritorna df piatto, niente
    regressioni rispetto al comportamento legacy."""
    svc = _make_service(group_id="grp-1")
    df = pd.DataFrame({"id": [1, 2]})

    with patch("app.shared.sharing.is_resource_visible", return_value=True), \
         patch("app.services.file_service.file_source_service") as svc_fs, \
         patch("app.core.arricchisci.api_connector.filter_visible_columns",
               side_effect=lambda d, _cols: d):
        svc_fs.load_dataframe.return_value = (df, None)
        out = svc._load_file_source({"file_source_id": "fs-flat", "columns": []})

    assert out is df


def test_load_file_source_blocks_cross_tenant_via_loader():
    """Visibility check enforced dal loader: file non visibile →
    ValueError dal wrapper (tradotto da SourceLoadingError)."""
    svc = _make_service(group_id="grp-1")

    with patch("app.shared.sharing.is_resource_visible", return_value=False), \
         patch("app.services.file_service.file_source_service") as svc_fs:
        with pytest.raises(ValueError, match="non appartiene al tuo gruppo"):
            svc._load_file_source({"file_source_id": "fs-99"})

    # Il loader NON deve aver tentato il load_dataframe
    svc_fs.load_dataframe.assert_not_called()


def test_load_file_source_fail_closed_without_current_group_id():
    svc = _make_service(group_id=None)
    with pytest.raises(ValueError, match="Contesto di sicurezza"):
        svc._load_file_source({"file_source_id": "fs-1"})


# ---------------------------------------------------------------------------
# Static values: nessuna regressione
# ---------------------------------------------------------------------------


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
