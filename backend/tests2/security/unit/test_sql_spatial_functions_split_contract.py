from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


@pytest.mark.parametrize(
    "db,function_name,params,expected",
    [
        ("postgres", "CENTROID", {"column": "geom"}, "ST_Centroid(t.geom)"),
        ("mssql", "BUFFER", {"column": "geom", "distance": 10}, "t.geom.STBuffer(10.0)"),
        ("mysql", "TRANSFORM", {"column": "geom", "srid": 4326}, "ST_Transform(t.geom, 4326)"),
        ("oracle", "AREA", {"column": "geom"}, "SDO_GEOM.SDO_AREA(t.geom, 0.005)"),
        ("sqlite", "LENGTH", {"column": "geom"}, "GLength(t.geom)"),
        ("mysql", "PERIMETER", {"column": "geom"}, "ST_Length(ST_ExteriorRing(t.geom))"),
        ("postgres", "SIMPLIFY", {"column": "geom", "tolerance": 0.5}, "ST_Simplify(t.geom, 0.5)"),
        ("mssql", "CONVEX_HULL", {"column": "geom"}, "t.geom.STConvexHull()"),
        ("sqlite", "ENVELOPE", {"column": "geom"}, "Envelope(t.geom)"),
    ],
)
def test_spatial_function_translation_stays_compatible(
    db,
    function_name,
    params,
    expected,
):
    from app.db.sql_functions import get_translator

    translator = get_translator(db)

    assert translator.translate(function_name, params, table_alias="t") == expected


def test_spatial_functions_remain_available_from_sql_functions_public_surface():
    from app.db.sql_functions import translate_calculated_column

    expression = {"function": "BUFFER", "params": {"column": "geom", "distance": 10}}

    assert (
        translate_calculated_column("postgres", "buffer_geom", expression, table_alias="t")
        == "ST_AsText(ST_Buffer(t.geom, 10.0)) AS buffer_geom"
    )


def test_spatial_function_translators_live_outside_sql_functions_module():
    import app.db.sql_functions as sql_functions
    import app.db.sql_spatial_functions as sql_spatial_functions

    assert issubclass(
        sql_functions.SqlFunctionTranslator,
        sql_spatial_functions.SqlSpatialFunctionMixin,
    )

    sql_functions_source = Path(sql_functions.__file__).read_text(encoding="utf-8")
    split_source = Path(sql_spatial_functions.__file__).read_text(encoding="utf-8")

    for method_name in (
        "centroid",
        "buffer",
        "transform",
        "area",
        "length",
        "perimeter",
        "simplify",
        "convex_hull",
        "envelope",
    ):
        definition = f"def _translate_{method_name}"
        assert definition not in sql_functions_source
        assert definition in split_source


@pytest.mark.parametrize(
    "function_name,params",
    [
        ("BUFFER", {"column": "geom", "distance": "10); DROP TABLE x;--"}),
        ("TRANSFORM", {"column": "geom", "srid": "4326; DELETE FROM x--"}),
        ("SIMPLIFY", {"column": "geom", "tolerance": "0.5 OR 1=1"}),
    ],
)
def test_spatial_split_keeps_injection_guards(function_name, params):
    from app.db.sql_functions import get_translator

    translator = get_translator("postgres")

    with pytest.raises(ValueError):
        translator.translate(function_name, params, table_alias="t")
