"""Smoke test per ``runner/factories.py`` e ``runner/parametrize.py``.

Questi test girano **senza infrastruttura** (no docker, no app plenora):
verificano solo che le factory ritornino dict ben formati e che gli
helper ``parametrize_*`` producano collezioni di parametri pytest validi.

Marker ``@pytest.mark.smoke`` — definito in ``pytest.ini``.
"""

from __future__ import annotations

import pytest

# Le factory sono pure dict-builders → import diretto.
from runner import factories as F
from runner.parametrize import (
    ALL_DB_DIALECTS,
    ALL_DIMENSIONS,
    CORE_DIALECTS,
    FILE_FORMATS_ALL,
    FILE_FORMATS_SPATIAL,
    FILE_FORMATS_TABULAR,
    GEOMETRY_TYPES,
    HEAVY_DIALECTS,
    LIGHT_DIALECTS,
    PAGINATION_STYLES,
    PARAMETRIZE_HELPERS,
    REST_AUTH_STYLES,
    parametrize_auth_styles,
    parametrize_dialects,
    parametrize_file_formats,
    parametrize_geometry_types,
    parametrize_join_types,
    parametrize_pagination_styles,
    parametrize_project_types,
    parametrize_spatial_predicates,
    parametrize_write_modes,
)

# Le fixture parametrizzate (`db_connection_for_dialect`, ecc.) vengono
# importate qui per essere visibili a pytest senza modificare conftest.py.
# Pytest le raccoglie da qualsiasi import nel test module.
from runner.parametrized_fixtures import (  # noqa: F401  (fixture import)
    api_conn_for_auth_style,
    datasource_for_format,
    db_connection_for_dialect,
)


# ── Builders availability ─────────────────────────────────────────────

def test_builders_available_flag_is_bool() -> None:
    """``BUILDERS_AVAILABLE`` deve essere un bool, anche se i builders
    di Agent B non sono ancora pronti."""
    assert isinstance(F.BUILDERS_AVAILABLE, bool)


# ── Canvas factories: shape e contenuto ───────────────────────────────

@pytest.mark.smoke
def test_factories_canvas_empty_shape() -> None:
    canvas = F.canvas_empty()
    assert canvas == {"nodes": [], "edges": []}


@pytest.mark.smoke
def test_factories_canvas_estrai_postgres() -> None:
    canvas = F.canvas_estrai_postgres_customers(connection_id="dummy-id")
    assert "nodes" in canvas
    assert "edges" in canvas
    assert len(canvas["nodes"]) >= 1
    # Il nodo source ha type "source" (vedi canvas_models.Node literal)
    source_nodes = [n for n in canvas["nodes"] if n["type"] == "source"]
    assert len(source_nodes) == 1
    assert source_nodes[0]["connection_id"] == "dummy-id"
    assert source_nodes[0]["table_name"] == "customers"
    # Almeno un edge che termina sul result
    assert len(canvas["edges"]) >= 1


@pytest.mark.smoke
def test_factories_canvas_estrai_with_filter() -> None:
    canvas = F.canvas_estrai_with_filter(
        connection_id="cid",
        table="orders",
        column="total_amount",
        operator=">",
        value=100,
    )
    src = next(n for n in canvas["nodes"] if n["type"] == "source")
    assert src["filters"], "filtro non presente nel nodo source"
    assert src["filters"][0]["column"] == "total_amount"
    assert src["filters"][0]["operator"] == ">"


@pytest.mark.smoke
def test_factories_canvas_join_3_tables() -> None:
    canvas = F.canvas_join_customers_cities_regions(connection_id="cid")
    sources = [n for n in canvas["nodes"] if n["type"] == "source"]
    assert len(sources) == 3, f"attesi 3 source, trovati {len(sources)}"
    joins = [n for n in canvas["nodes"] if n["type"] == "multi_join"]
    assert len(joins) == 1
    # 3 edge dai source al join + 1 dal join al result = 4
    assert len(canvas["edges"]) >= 4


@pytest.mark.smoke
def test_factories_canvas_spatial_within() -> None:
    canvas = F.canvas_spatial_within(connection_id="cid")
    spatial_edges = [e for e in canvas["edges"] if e.get("is_spatial")]
    assert spatial_edges, "atteso almeno un edge spatial"
    assert spatial_edges[0]["spatial_predicate"] == "ST_Within"


@pytest.mark.smoke
def test_factories_canvas_estrai_arricchisci_carica() -> None:
    canvas = F.canvas_estrai_arricchisci_carica(
        postgres_conn_id="pg", api_conn_id="api"
    )
    types = {n["type"] for n in canvas["nodes"]}
    assert "source" in types
    assert "api_enrich" in types
    assert "carica" in types


@pytest.mark.smoke
def test_factories_canvas_csv_to_postgres() -> None:
    canvas = F.canvas_csv_to_postgres(
        datasource_id="ds",
        csv_path="/in/customers.csv",
        conn_id="pg",
        target_table="customers_imported",
    )
    ds_nodes = [n for n in canvas["nodes"] if n["type"] == "datasource_node"]
    assert ds_nodes
    assert ds_nodes[0]["format"] == "csv"
    load_nodes = [n for n in canvas["nodes"] if n["type"] == "carica"]
    assert load_nodes
    assert load_nodes[0]["table_name"] == "customers_imported"


@pytest.mark.smoke
def test_factories_canvas_geojson_with_geo_filter() -> None:
    canvas = F.canvas_geojson_with_geo_filter(
        datasource_id="ds",
        geojson_path="/in/regions.geojson",
        region_polygon_wkt=F.lombardia_polygon_wkt(),
    )
    transform_nodes = [
        n for n in canvas["nodes"] if n["type"] == "transform"
    ]
    assert transform_nodes
    assert transform_nodes[0]["transform_op"] == "geo_clip"


@pytest.mark.smoke
def test_factories_canvas_aggregate() -> None:
    canvas = F.canvas_aggregate_sum_per_region(connection_id="cid")
    agg_nodes = [n for n in canvas["nodes"] if n.get("transform_op") == "aggregate"]
    assert agg_nodes
    assert agg_nodes[0]["aggregations"][0]["function"] == "SUM"


@pytest.mark.smoke
def test_factories_canvas_transform_filter_null() -> None:
    canvas = F.canvas_transform_filter_null(connection_id="cid")
    tr = next(n for n in canvas["nodes"] if n["type"] == "transform")
    assert tr["transform_op"] == "filter"


@pytest.mark.smoke
def test_factories_canvas_pagination_offset() -> None:
    canvas = F.canvas_pagination_offset(connection_id="cid", limit=500)
    src = next(n for n in canvas["nodes"] if n["type"] == "source")
    assert src["limit"] == 500


# ── Connection / project factories ────────────────────────────────────

@pytest.mark.smoke
def test_factories_all_db_connections_complete() -> None:
    payloads = F.all_db_connections()
    types = {p["type"] for p in payloads}
    assert types == {"postgres", "mysql", "mssql", "oracle"}
    # Ogni payload ha tutti i campi richiesti da ConnectionCreate
    for p in payloads:
        for key in ("name", "type", "host", "port", "database", "username", "password"):
            assert key in p, f"manca {key} in {p['type']}"


@pytest.mark.smoke
def test_factories_all_auth_api_connections_complete() -> None:
    payloads = F.all_auth_api_connections()
    auth_types = {p.get("auth_type") for p in payloads}
    expected = {
        "api_key",
        "bearer",
        "basic",
        "oauth2_client_credentials",
        "oauth2_password",
        "arcgis_token",
    }
    assert expected.issubset(auth_types), (
        f"auth_type mancanti: {expected - auth_types}"
    )


@pytest.mark.smoke
def test_factories_all_file_datasources() -> None:
    payloads = F.all_file_datasources()
    types = {p["type"] for p in payloads}
    assert {"ftp", "sftp"}.issubset(types)


@pytest.mark.smoke
def test_factories_project_payloads() -> None:
    pe = F.project_estrai_payload(name="x", default_connection_id="c")
    pa = F.project_arricchisci_payload(name="x", default_connection_id="c")
    pm = F.project_manipola_payload(name="x", default_connection_id="c")
    assert pe["project_type"] == "estrai"
    assert pa["project_type"] == "arricchisci"
    assert pm["project_type"] == "manipola"


# ── Dataset reference factories ───────────────────────────────────────

@pytest.mark.smoke
def test_factories_expected_customers_count_parametric() -> None:
    assert F.expected_customers_count() == 22  # 12 + 8 + 2
    assert F.expected_customers_count(include_edge=False) == 14
    assert F.expected_customers_count(include_edge=False, include_null=False) == 12


@pytest.mark.smoke
def test_factories_expected_table_counts() -> None:
    counts = F.expected_table_counts()
    assert counts.get("customers") == 22
    assert counts.get("events_log") == 10000
    assert counts.get("regions") == 4


@pytest.mark.smoke
def test_factories_expected_cities_within_lombardia() -> None:
    ids = F.expected_cities_within_lombardia()
    assert isinstance(ids, list)
    assert all(isinstance(i, int) for i in ids)
    # Almeno Milano (id=1) + Bergamo (id=2)
    assert 1 in ids
    assert 2 in ids


@pytest.mark.smoke
def test_factories_expected_cities_within_campania() -> None:
    ids = F.expected_cities_within_campania()
    # Napoli id=7
    assert 7 in ids


@pytest.mark.smoke
def test_factories_expected_customers_per_region() -> None:
    counts = F.expected_customers_per_region()
    # LOM è la regione più popolata nel seed
    assert counts.get("LOM", 0) > 0


@pytest.mark.smoke
def test_factories_expected_employees_max_depth() -> None:
    assert F.expected_employees_max_depth() == 5


@pytest.mark.smoke
def test_factories_expected_customers_without_orders_count() -> None:
    assert F.expected_customers_without_orders_count() >= 1


@pytest.mark.smoke
def test_factories_expected_customers_with_null_age_count() -> None:
    assert F.expected_customers_with_null_age_count() >= 1


# ── Geometry factories ────────────────────────────────────────────────

@pytest.mark.smoke
def test_factories_polygon_wkts() -> None:
    assert F.lombardia_polygon_wkt().startswith("POLYGON")
    assert F.campania_polygon_wkt().startswith("POLYGON")
    assert F.sample_point_wkt().startswith("POINT")


# ── Registry consistency ──────────────────────────────────────────────

@pytest.mark.smoke
def test_factories_registry_callable() -> None:
    """Ogni nome in ``ALL_FACTORIES`` deve esistere come callable nel modulo."""
    for name in F.ALL_FACTORIES:
        fn = getattr(F, name, None)
        assert callable(fn), f"{name} non è callable nel modulo factories"


# ── Parametrize helper: dimensioni ────────────────────────────────────

@pytest.mark.smoke
def test_parametrize_dimensions_non_empty() -> None:
    for name, values in ALL_DIMENSIONS.items():
        assert values, f"dimensione {name!r} vuota"
        # No duplicati
        assert len(values) == len(set(values)), f"duplicati in {name!r}"


@pytest.mark.smoke
def test_parametrize_light_subset_of_all_dialects() -> None:
    assert set(LIGHT_DIALECTS).issubset(ALL_DB_DIALECTS)
    assert set(HEAVY_DIALECTS).issubset(ALL_DB_DIALECTS)
    # Light + heavy = all
    assert set(LIGHT_DIALECTS) | set(HEAVY_DIALECTS) == set(ALL_DB_DIALECTS)


@pytest.mark.smoke
def test_parametrize_core_dialects_subset_of_light() -> None:
    assert set(CORE_DIALECTS).issubset(LIGHT_DIALECTS)


@pytest.mark.smoke
def test_parametrize_file_formats_partition() -> None:
    assert set(FILE_FORMATS_TABULAR).isdisjoint(FILE_FORMATS_SPATIAL)
    assert set(FILE_FORMATS_ALL) == set(FILE_FORMATS_TABULAR) | set(FILE_FORMATS_SPATIAL)


@pytest.mark.smoke
def test_parametrize_helpers_callable() -> None:
    """Ogni nome in ``PARAMETRIZE_HELPERS`` deve essere callable."""
    import runner.parametrize as P

    for name in PARAMETRIZE_HELPERS:
        fn = getattr(P, name, None)
        assert callable(fn), f"{name} non è callable in runner.parametrize"


@pytest.mark.smoke
def test_parametrize_dialects_returns_marker() -> None:
    """``parametrize_dialects()`` deve ritornare un decorator pytest valido."""
    decorator = parametrize_dialects(only_light=True)
    # pytest.mark.parametrize ritorna un MarkDecorator
    assert hasattr(decorator, "mark") or hasattr(decorator, "args")


@pytest.mark.smoke
def test_parametrize_dialects_exclude() -> None:
    decorator = parametrize_dialects(exclude=["oracle", "sqlite"])
    # Sanity: il decorator può essere applicato a una funzione
    applied = decorator(lambda dialect: None)
    assert applied is not None


@pytest.mark.smoke
def test_parametrize_auth_styles_arcgis_toggle() -> None:
    with_arcgis = parametrize_auth_styles(include_arcgis=True)
    without = parametrize_auth_styles(include_arcgis=False)
    assert with_arcgis is not None and without is not None


@pytest.mark.smoke
def test_parametrize_file_formats_kind_validation() -> None:
    # kind valido
    parametrize_file_formats(kind="tabular")
    parametrize_file_formats(kind="spatial")
    parametrize_file_formats(kind="all")
    # kind invalido
    with pytest.raises(ValueError):
        parametrize_file_formats(kind="bogus")


@pytest.mark.smoke
def test_parametrize_geometry_only_simple() -> None:
    parametrize_geometry_types(only_simple=True)
    parametrize_geometry_types(only_simple=False)


@pytest.mark.smoke
def test_parametrize_join_types_cross_toggle() -> None:
    parametrize_join_types(include_cross=True)
    parametrize_join_types(include_cross=False)


@pytest.mark.smoke
def test_parametrize_other_helpers_callable() -> None:
    parametrize_pagination_styles()
    parametrize_write_modes()
    parametrize_project_types()
    parametrize_spatial_predicates()


# ── Test parametrizzato esemplificativo ───────────────────────────────

@pytest.mark.smoke
@parametrize_dialects(only_light=True)
def test_extract_smoke_per_dialect(dialect: str) -> None:
    """Stesso smoke su 4 DB. Per ora skip — dimostrazione del helper."""
    assert dialect in LIGHT_DIALECTS
    pytest.skip(f"factory smoke per {dialect}: TBD (richiede infra)")


@pytest.mark.smoke
@parametrize_auth_styles(include_arcgis=True)
def test_auth_smoke_per_style(auth_style: str) -> None:
    """Demo: stesso test su tutti gli auth REST."""
    assert auth_style in REST_AUTH_STYLES
    pytest.skip(f"auth smoke per {auth_style}: TBD (richiede fake-rest)")


@pytest.mark.smoke
@parametrize_file_formats(kind="tabular")
def test_file_smoke_per_format(file_format: str) -> None:
    """Demo: stesso test su ogni formato file tabulare."""
    assert file_format in FILE_FORMATS_TABULAR
    pytest.skip(f"file format smoke per {file_format}: TBD (richiede SFTP)")


@pytest.mark.smoke
@parametrize_geometry_types(only_simple=True)
def test_geometry_smoke_per_type(geometry_type: str) -> None:
    """Demo: stesso test su Point/LineString/Polygon."""
    assert geometry_type in GEOMETRY_TYPES
    pytest.skip(f"geometry smoke per {geometry_type}: TBD (richiede PostGIS)")


@pytest.mark.smoke
@parametrize_pagination_styles()
def test_pagination_smoke_per_style(pagination_style: str) -> None:
    """Demo: stesso test su ogni stile paginazione REST."""
    assert pagination_style in PAGINATION_STYLES
    pytest.skip(f"pagination smoke per {pagination_style}: TBD")
