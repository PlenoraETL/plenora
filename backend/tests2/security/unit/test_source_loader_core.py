"""
Core-level contract test per Refactor #3 SourceLoader.

`SourceLoader` e' il dispatch unico per file_source / project_source /
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.artifacts import (
    ArtifactResolver,
    LatestProjectArtifactRef,
    MaterializedArtifact,
    SourceLoader,
    SourceLoadingError,
    SourceRef,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


# 1. expected_group_id obbligatorio


def test_load_requires_expected_group_id_keyword():
    """expected_group_id e' keyword-only senza default: ometterlo
    deve sollevare TypeError, non passare silente."""
    loader = SourceLoader()
    ref = SourceRef(static_fields=tuple())
    with pytest.raises(TypeError):
        loader.load(ref)  # type: ignore[call-arg]


def test_load_rejects_blank_expected_group_id():
    """None / stringa vuota: ValueError esplicito.
    Pre-Refactor #3 i call site duplicati avevano default opzionali
    che disabilitavano il check del resolver downstream."""
    loader = SourceLoader()
    ref = SourceRef(file_source_id="fs-1")
    with pytest.raises(ValueError, match="expected_group_id obbligatorio"):
        loader.load(ref, expected_group_id="")
    with pytest.raises(ValueError, match="expected_group_id obbligatorio"):
        loader.load(ref, expected_group_id=None)  # type: ignore[arg-type]


# 2. branch project propaga expected_group_id al resolver


def test_load_project_propagates_expected_group_id_to_resolver():
    """Test diretto: il loader DEVE chiamare materialize_latest con
    expected_group_id non-None. Senza questo il check cross-tenant
    nel resolver e' disabilitato (era il fail-OPEN di
    api_connector pre-Refactor #3)."""
    resolver = MagicMock(spec=ArtifactResolver)
    resolver.materialize_latest.return_value = None
    loader = SourceLoader(artifact_resolver=resolver)

    ref = SourceRef(project=LatestProjectArtifactRef(project_id="proj-1"))
    loader.load(ref, expected_group_id="grp-A")

    resolver.materialize_latest.assert_called_once()
    call_args = resolver.materialize_latest.call_args
    assert call_args.kwargs.get("expected_group_id") == "grp-A"


# 3. branch project mismatch → block


def test_load_project_blocks_cross_tenant_via_resolver():
    """End-to-end con ArtifactResolver reale (mockando solo leaf DAL):
    cross-tenant solleva SourceLoadingError senza rivelare il tenant reale."""
    import app.core.artifacts.db_boundary as artifact_db
    import app.core.results.db as results_db

    loader = SourceLoader()
    ref = SourceRef(project=LatestProjectArtifactRef(project_id="proj-1"))

    with patch.object(artifact_db, "get_project_for_artifact", return_value=None), \
         patch.object(results_db, "get_latest_result_row", return_value=None):
        with pytest.raises(SourceLoadingError, match="non trovato"):
            loader.load(ref, expected_group_id="grp-mine")


def test_load_project_returns_none_when_no_latest_result():
    """Sanity: same-group, no result disponibile → None (semantica
    legacy di get_result_as_tempfile, non eccezione)."""
    import app.core.artifacts.db_boundary as artifact_db
    import app.core.results.db as results_db

    loader = SourceLoader()
    ref = SourceRef(project=LatestProjectArtifactRef(project_id="proj-1"))

    with patch.object(artifact_db, "get_project_for_artifact",
                      return_value=object()), \
         patch.object(results_db, "get_latest_result_row", return_value=None):
        result = loader.load(ref, expected_group_id="grp-mine")
    assert result is None


# resolve_project_path: variante "ritorna path" del branch project


def test_resolve_project_path_requires_expected_group_id():
    loader = SourceLoader()
    ref = LatestProjectArtifactRef(project_id="proj-1")
    with pytest.raises(ValueError, match="expected_group_id obbligatorio"):
        loader.resolve_project_path(ref, expected_group_id="")
    with pytest.raises(ValueError, match="expected_group_id obbligatorio"):
        loader.resolve_project_path(ref, expected_group_id=None)  # type: ignore[arg-type]


def test_resolve_project_path_propagates_group_id_to_resolver():
    """Stesso contratto di _load_project: il group_id deve arrivare
    a materialize_latest, altrimenti il check cross-tenant nel
    resolver e' disabilitato."""
    resolver = MagicMock(spec=ArtifactResolver)
    resolver.materialize_latest.return_value = None
    loader = SourceLoader(artifact_resolver=resolver)

    ref = LatestProjectArtifactRef(project_id="proj-1")
    loader.resolve_project_path(ref, expected_group_id="grp-A")

    resolver.materialize_latest.assert_called_once()
    call_args = resolver.materialize_latest.call_args
    assert call_args.kwargs.get("expected_group_id") == "grp-A"


def test_resolve_project_path_blocks_cross_tenant_via_resolver():
    """End-to-end con resolver reale: cross-tenant solleva
    SourceLoadingError, NON ritorna silenziosamente un path."""
    import app.core.artifacts.db_boundary as artifact_db
    import app.core.results.db as results_db

    loader = SourceLoader()
    ref = LatestProjectArtifactRef(project_id="proj-1")

    with patch.object(artifact_db, "get_project_for_artifact", return_value=None), \
         patch.object(results_db, "get_latest_result_row", return_value=None):
        with pytest.raises(SourceLoadingError, match="non trovato"):
            loader.resolve_project_path(ref, expected_group_id="grp-mine")


def test_resolve_project_path_returns_none_when_no_latest_result():
    """Sanity: same-group, no result → None, niente eccezione."""
    import app.core.artifacts.db_boundary as artifact_db
    import app.core.results.db as results_db

    loader = SourceLoader()
    ref = LatestProjectArtifactRef(project_id="proj-1")

    with patch.object(artifact_db, "get_project_for_artifact",
                      return_value=object()), \
         patch.object(results_db, "get_latest_result_row", return_value=None):
        result = loader.resolve_project_path(ref, expected_group_id="grp-mine")
    assert result is None


def test_resolve_project_path_returns_path_string_when_materialized():
    """Sanity: same-group + result presente → ritorna str(path)."""
    materialized_path = "/tmp/proj-1.csv"
    materialized = MaterializedArtifact(
        path=__import__("pathlib").Path(materialized_path),
        format="csv",
        is_temporary=True,
    )
    resolver = MagicMock(spec=ArtifactResolver)
    resolver.materialize_latest.return_value = materialized
    loader = SourceLoader(artifact_resolver=resolver)

    ref = LatestProjectArtifactRef(project_id="proj-1")
    result = loader.resolve_project_path(ref, expected_group_id="grp-mine")
    assert result is not None
    # Su Windows pathlib normalizza / e \, confronto sul nome
    assert result.endswith("proj-1.csv")


# 4. branch file: visibility check enforced dal loader stesso


def test_load_file_blocks_invisible_resource():
    """Cross-tenant: is_resource_visible ritorna False → SourceLoadingError.
    Il loader NON deve neanche tentare il load_dataframe."""
    loader = SourceLoader()
    ref = SourceRef(file_source_id="fs-99")

    with patch("app.core.auth.visibility.is_resource_visible", return_value=False) as visibility, \
         patch("app.core.files.service.file_source_service") as svc:
        with pytest.raises(SourceLoadingError, match="non appartiene al tuo gruppo"):
            loader.load(ref, expected_group_id="grp-1")

    visibility.assert_called_once_with(
        "file_sources", "fs-99", "grp-1", is_superadmin=False,
    )
    svc.load_dataframe.assert_not_called()


def test_load_file_visibility_check_exception_is_fail_closed():
    """is_resource_visible esplode (DB down ecc.) → SourceLoadingError.
    Pre-Refactor #2 manipola swallowava con un warning: ora il loader
    enforcing fa hard fail."""
    loader = SourceLoader()
    ref = SourceRef(file_source_id="fs-1")

    def _boom(*_a, **_kw):
        raise RuntimeError("DB down")

    with patch("app.core.auth.visibility.is_resource_visible", side_effect=_boom):
        with pytest.raises(SourceLoadingError, match="Verifica visibilita'"):
            loader.load(ref, expected_group_id="grp-1")


# 5. branch file: GDF preservato se presente


def test_load_file_returns_geodataframe_when_available():
    """Il GeoDataFrame ha priorita' sul DataFrame piatto: senza questo
    le trasformazioni downstream perdono il CRS. Bug rilevato in
    api_connector._load_file_source, ora chiuso dal loader unico."""
    loader = SourceLoader()
    ref = SourceRef(file_source_id="fs-spatial")
    df = pd.DataFrame({"id": [1]})
    gdf = object()  # sentinella, non importa il tipo concreto

    with patch("app.core.auth.visibility.is_resource_visible", return_value=True), \
         patch("app.core.files.service.file_source_service") as svc:
        svc.load_dataframe.return_value = (df, gdf)
        out = loader.load(ref, expected_group_id="grp-1")

    assert out is gdf


def test_load_file_returns_dataframe_when_no_geodataframe():
    """Sanity: file non geografico → ritorna df piatto."""
    loader = SourceLoader()
    ref = SourceRef(file_source_id="fs-flat")
    df = pd.DataFrame({"id": [1, 2]})

    with patch("app.core.auth.visibility.is_resource_visible", return_value=True), \
         patch("app.core.files.service.file_source_service") as svc:
        svc.load_dataframe.return_value = (df, None)
        out = loader.load(ref, expected_group_id="grp-1")

    assert out is df


# 6. branch static: output coerente, no dipendenze HTTP/user


def test_load_static_with_simple_fields():
    loader = SourceLoader()
    ref = SourceRef(static_fields=tuple([
        {"name": "city", "value": "Roma"},
        {"name": "code", "value": "42"},
    ]))
    df = loader.load(ref, expected_group_id="grp-1")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["city"] == "Roma"
    # process_static_fields lascia 'value' come stringa per default
    assert str(row["code"]) == "42"


def test_load_static_with_empty_fields_returns_single_empty_row():
    loader = SourceLoader()
    ref = SourceRef(static_fields=tuple())
    df = loader.load(ref, expected_group_id="grp-1")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1


def test_load_static_does_not_touch_db_or_http():
    """Static branch deve essere puro processing, niente DB/HTTP/user.
    Verifichiamo che NON faccia chiamate a is_resource_visible o a
    file_source_service."""
    loader = SourceLoader()
    ref = SourceRef(static_fields=tuple([{"name": "x", "value": "1"}]))

    with patch("app.core.auth.visibility.is_resource_visible") as visibility, \
         patch("app.core.files.service.file_source_service") as svc, \
         patch("app.core.artifacts.db_boundary.get_project_for_artifact") as get_project:
        loader.load(ref, expected_group_id="grp-1")

    visibility.assert_not_called()
    svc.load_dataframe.assert_not_called()
    get_project.assert_not_called()


# 7. SourceRef enforced exactly-one-source


def test_source_ref_rejects_zero_sources():
    with pytest.raises(ValueError, match="vuoto"):
        SourceRef()


def test_source_ref_rejects_two_sources():
    with pytest.raises(ValueError, match="ambiguo"):
        SourceRef(
            file_source_id="fs-1",
            project=LatestProjectArtifactRef(project_id="p1"),
        )


def test_source_ref_rejects_three_sources():
    with pytest.raises(ValueError, match="ambiguo"):
        SourceRef(
            file_source_id="fs-1",
            project=LatestProjectArtifactRef(project_id="p1"),
            static_fields=tuple([{"name": "x", "value": "1"}]),
        )


def test_source_ref_kind_dispatch():
    assert SourceRef(file_source_id="fs-1").kind() == "file"
    assert SourceRef(project=LatestProjectArtifactRef(project_id="p1")).kind() == "project"
    assert SourceRef(static_fields=tuple([{"name": "x", "value": "1"}])).kind() == "static"


# Bonus: from_node_data factory


def test_from_node_data_project_source():
    ref = SourceRef.from_node_data("project_source", {"source_project_id": "proj-1"})
    assert ref.kind() == "project"
    assert ref.project.project_id == "proj-1"


def test_from_node_data_file_source():
    ref = SourceRef.from_node_data("file_source", {"file_source_id": "fs-1"})
    assert ref.kind() == "file"
    assert ref.file_source_id == "fs-1"


def test_from_node_data_static_values_camel_and_snake_case():
    """Il canvas usa 'staticValues' (camelCase), test entrambi per
    coprire eventuali drift di convenzione."""
    fields = [{"name": "x", "value": "1"}]
    ref1 = SourceRef.from_node_data("staticValues", {"fields": fields})
    ref2 = SourceRef.from_node_data("static_values", {"fields": fields})
    assert ref1.kind() == "static"
    assert ref2.kind() == "static"


def test_from_node_data_unknown_type_raises():
    with pytest.raises(ValueError, match="node_type sconosciuto"):
        SourceRef.from_node_data("unknown", {})


def test_from_node_data_project_source_without_id_raises():
    with pytest.raises(ValueError, match="senza source_project_id"):
        SourceRef.from_node_data("project_source", {})


def test_from_node_data_file_source_without_id_raises():
    with pytest.raises(ValueError, match="senza file_source_id"):
        SourceRef.from_node_data("file_source", {})
