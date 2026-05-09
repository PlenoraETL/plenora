"""Consolidated SQL function split contract.

Pattern identico ripetuto per le 4 categorie (date / numeric / spatial /
string): translator dispatcher su 5 dialetti, surface pubblica via
`translate_calculated_column`, mixin estratto fuori da
`app.db.sql_functions`, injection guard dove rilevante.

Era diviso in 4 file (`test_sql_<cat>_functions_split_contract.py`)
con 95-100 righe ciascuno e zero differenze strutturali. Consolidato
in unico file con triple parametrizzazione (db x category x function).
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# Translation matrix: per-category sample covering 5 dialetti SQL.
# Ogni tupla: (db, category, function_name, params, expected_sql).
_TRANSLATION_CASES = [
    # date
    ("postgres", "date", "YEAR", {"column": "created_at"}, "EXTRACT(YEAR FROM t.created_at)"),
    ("sqlite", "date", "MONTH", {"column": "created_at"}, "CAST(STRFTIME('%m', t.created_at) AS INTEGER)"),
    ("oracle", "date", "NOW", {}, "SYSDATE"),
    ("mssql", "date", "CURRENT_DATE", {}, "CAST(GETDATE() AS DATE)"),
    (
        "mysql", "date", "DATE_DIFF",
        {"column": "end_at", "column2": "start_at"},
        "DATEDIFF(t.end_at, t.start_at)",
    ),
    (
        "postgres", "date", "DATE_ADD",
        {"column": "created_at", "interval": 3, "unit": "day"},
        "t.created_at + INTERVAL '3 day'",
    ),
    (
        "mysql", "date", "DATE_TRUNC",
        {"column": "created_at", "unit": "week"},
        "DATE(DATE_SUB(t.created_at, INTERVAL WEEKDAY(t.created_at) DAY))",
    ),
    (
        "sqlite", "date", "DATE_FORMAT",
        {"column": "created_at", "format": "YYYY-MM-DD"},
        "STRFTIME('%Y-%m-%d', t.created_at)",
    ),
    (
        "mssql", "date", "EXTRACT",
        {"column": "created_at", "part": "hour"},
        "DATEPART(hour, t.created_at)",
    ),
    # numeric
    ("postgres", "numeric", "ROUND", {"column": "amount", "decimals": 2}, "ROUND(t.amount::numeric, 2)"),
    ("mssql", "numeric", "CEIL", {"column": "amount"}, "CEILING(t.amount)"),
    ("sqlite", "numeric", "MOD", {"column": "amount", "divisor": 3}, "(t.amount % NULLIF(3.0, 0))"),
    (
        "mysql", "numeric", "MATH",
        {"column1": "a", "operator": "/", "value": 2},
        "IF(2.0 = 0, NULL, t.a / 2.0)",
    ),
    ("postgres", "numeric", "POWER", {"column": "amount", "exponent": 2}, "POWER(t.amount, 2.0)"),
    ("postgres", "numeric", "SQRT", {"column": "amount"}, "SQRT(t.amount)"),
    ("postgres", "numeric", "SIGN", {"column": "amount"}, "SIGN(t.amount)"),
    ("sqlite", "numeric", "GREATEST", {"values": "a,b"}, "MAX(t.a, t.b)"),
    ("sqlite", "numeric", "LEAST", {"values": ["a", "b"]}, "MIN(t.a, t.b)"),
    # spatial
    ("postgres", "spatial", "CENTROID", {"column": "geom"}, "ST_Centroid(t.geom)"),
    ("mssql", "spatial", "BUFFER", {"column": "geom", "distance": 10}, "t.geom.STBuffer(10.0)"),
    ("mysql", "spatial", "TRANSFORM", {"column": "geom", "srid": 4326}, "ST_Transform(t.geom, 4326)"),
    ("oracle", "spatial", "AREA", {"column": "geom"}, "SDO_GEOM.SDO_AREA(t.geom, 0.005)"),
    ("sqlite", "spatial", "LENGTH", {"column": "geom"}, "GLength(t.geom)"),
    (
        "mysql", "spatial", "PERIMETER",
        {"column": "geom"},
        "ST_Length(ST_ExteriorRing(t.geom))",
    ),
    (
        "postgres", "spatial", "SIMPLIFY",
        {"column": "geom", "tolerance": 0.5},
        "ST_Simplify(t.geom, 0.5)",
    ),
    ("mssql", "spatial", "CONVEX_HULL", {"column": "geom"}, "t.geom.STConvexHull()"),
    ("sqlite", "spatial", "ENVELOPE", {"column": "geom"}, "Envelope(t.geom)"),
    # string
    ("postgres", "string", "UPPER", {"column": "name"}, "UPPER(t.name)"),
    ("mssql", "string", "TRIM", {"column": "name"}, "LTRIM(RTRIM(t.name))"),
    (
        "postgres", "string", "CONCAT",
        {"values": "first,last", "separator": "-"},
        "CONCAT_WS('-', t.first, t.last)",
    ),
    (
        "oracle", "string", "CONCAT",
        {"values": ["first", "last"], "separator": "-"},
        "t.first || '-' || t.last",
    ),
    (
        "sqlite", "string", "SUBSTRING",
        {"column": "name", "start": 2, "length": 3},
        "SUBSTR(t.name, 2, 3)",
    ),
    ("oracle", "string", "LEFT", {"column": "name", "length": 4}, "SUBSTR(t.name, 1, 4)"),
    (
        "mysql", "string", "POSITION",
        {"column": "name", "search": "x"},
        "LOCATE('x', t.name)",
    ),
    (
        "postgres", "string", "REPLACE",
        {"column": "name", "search": "O'Hara", "replace": "OH"},
        "REPLACE(t.name, 'O''Hara', 'OH')",
    ),
]


@pytest.mark.parametrize(
    "db,category,function_name,params,expected",
    _TRANSLATION_CASES,
    ids=[
        f"{db}-{cat}-{fn}"
        for db, cat, fn, _params, _expected in _TRANSLATION_CASES
    ],
)
def test_function_translation_stays_compatible(
    db, category, function_name, params, expected,
):
    """Per ogni categoria SQL function (date/numeric/spatial/string) e
    per ogni dialetto target, la traduzione canonica del translator
    resta stabile. Coverage matrix-style su tutti i dialetti."""
    from app.db.sql_functions import get_translator

    translator = get_translator(db)

    assert translator.translate(function_name, params, table_alias="t") == expected


# Public-surface sample: una funzione per categoria.
@pytest.mark.parametrize(
    "category,expression,expected",
    [
        (
            "date",
            {"function": "YEAR", "params": {"column": "created_at"}},
            "EXTRACT(YEAR FROM t.created_at) AS created_year",
        ),
        (
            "numeric",
            {"function": "ABS", "params": {"column": "amount"}},
            "ABS(t.amount) AS abs_amount",
        ),
        (
            "spatial",
            {"function": "BUFFER", "params": {"column": "geom", "distance": 10}},
            "ST_AsText(ST_Buffer(t.geom, 10.0)) AS buffer_geom",
        ),
        (
            "string",
            {"function": "UPPER", "params": {"column": "name"}},
            "UPPER(t.name) AS upper_name",
        ),
    ],
)
def test_functions_remain_available_from_sql_functions_public_surface(
    category, expression, expected,
):
    """Tutte le 4 famiglie di funzioni SQL restano accessibili via
    `translate_calculated_column`, la public surface ufficiale del
    modulo `app.db.sql_functions`."""
    from app.db.sql_functions import translate_calculated_column

    alias_map = {
        "date": "created_year",
        "numeric": "abs_amount",
        "spatial": "buffer_geom",
        "string": "upper_name",
    }
    alias = alias_map[category]
    assert (
        translate_calculated_column("postgres", alias, expression, table_alias="t")
        == expected
    )


# Module-extraction guard: ogni mixin vive nel suo file dedicato e
# `app.db.sql_functions` NON contiene piu' le definizioni dei translator.
@pytest.mark.parametrize(
    "category,split_module,mixin_attr,methods",
    [
        (
            "date",
            "app.db.sql_date_functions",
            "SqlDateFunctionMixin",
            (
                "year", "month", "day", "now", "current_date",
                "date_diff", "date_add", "date_trunc", "date_format", "extract",
            ),
        ),
        (
            "numeric",
            "app.db.sql_numeric_functions",
            "SqlNumericFunctionMixin",
            (
                "round", "abs", "ceil", "floor", "math", "mod",
                "power", "sqrt", "sign", "greatest", "least",
            ),
        ),
        (
            "spatial",
            "app.db.sql_spatial_functions",
            "SqlSpatialFunctionMixin",
            (
                "centroid", "buffer", "transform", "area", "length",
                "perimeter", "simplify", "convex_hull", "envelope",
            ),
        ),
        (
            "string",
            "app.db.sql_string_functions",
            "SqlStringFunctionMixin",
            (
                "upper", "lower", "trim", "concat", "substring",
                "replace", "left", "right", "position", "ltrim", "rtrim",
            ),
        ),
    ],
)
def test_function_translators_live_outside_sql_functions_module(
    category, split_module, mixin_attr, methods,
):
    """Per ogni categoria, il mixin estratto vive nel modulo dedicato
    e `SqlFunctionTranslator` lo eredita. Il modulo monolitico
    `app.db.sql_functions` non contiene piu' i `_translate_*`."""
    import importlib

    import app.db.sql_functions as sql_functions

    split = importlib.import_module(split_module)
    mixin = getattr(split, mixin_attr)

    assert issubclass(sql_functions.SqlFunctionTranslator, mixin)

    sql_functions_source = Path(sql_functions.__file__).read_text(encoding="utf-8")
    split_source = Path(split.__file__).read_text(encoding="utf-8")

    for method_name in methods:
        definition = f"def _translate_{method_name}"
        assert definition not in sql_functions_source, (
            f"{category}: {method_name} ancora in sql_functions.py"
        )
        assert definition in split_source, (
            f"{category}: {method_name} mancante da {split_module}"
        )


# Injection guard: per le categorie dove il translator accetta
# parametri scalari (numeric / spatial), il payload sospetto
# scatena ValueError invece di leak SQL.
@pytest.mark.parametrize(
    "category,function_name,params",
    [
        ("numeric", "MATH", {"column1": "a", "operator": "+", "value": "1); DROP TABLE x;--"}),
        ("numeric", "MOD", {"column": "a", "divisor": "3); DROP TABLE x;--"}),
        ("numeric", "POWER", {"column": "a", "exponent": "2); DROP TABLE x;--"}),
        ("spatial", "BUFFER", {"column": "geom", "distance": "10); DROP TABLE x;--"}),
        ("spatial", "TRANSFORM", {"column": "geom", "srid": "4326; DELETE FROM x--"}),
        ("spatial", "SIMPLIFY", {"column": "geom", "tolerance": "0.5 OR 1=1"}),
    ],
    ids=lambda val: val if isinstance(val, str) else None,
)
def test_split_keeps_injection_guards(category, function_name, params):
    """Dopo lo split per categorie, gli injection guard restano
    attivi: parametri scalari sospetti devono sollevare ValueError."""
    from app.db.sql_functions import get_translator

    translator = get_translator("postgres")

    with pytest.raises(ValueError):
        translator.translate(function_name, params, table_alias="t")
