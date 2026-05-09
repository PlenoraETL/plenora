from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


@pytest.mark.parametrize(
    "db,function_name,params,expected",
    [
        ("postgres", "UPPER", {"column": "name"}, "UPPER(t.name)"),
        ("mssql", "TRIM", {"column": "name"}, "LTRIM(RTRIM(t.name))"),
        (
            "postgres",
            "CONCAT",
            {"values": "first,last", "separator": "-"},
            "CONCAT_WS('-', t.first, t.last)",
        ),
        (
            "oracle",
            "CONCAT",
            {"values": ["first", "last"], "separator": "-"},
            "t.first || '-' || t.last",
        ),
        (
            "sqlite",
            "SUBSTRING",
            {"column": "name", "start": 2, "length": 3},
            "SUBSTR(t.name, 2, 3)",
        ),
        ("oracle", "LEFT", {"column": "name", "length": 4}, "SUBSTR(t.name, 1, 4)"),
        ("mysql", "POSITION", {"column": "name", "search": "x"}, "LOCATE('x', t.name)"),
        (
            "postgres",
            "REPLACE",
            {"column": "name", "search": "O'Hara", "replace": "OH"},
            "REPLACE(t.name, 'O''Hara', 'OH')",
        ),
    ],
)
def test_string_function_translation_stays_compatible(
    db,
    function_name,
    params,
    expected,
):
    from app.db.sql_functions import get_translator

    translator = get_translator(db)

    assert translator.translate(function_name, params, table_alias="t") == expected


def test_string_functions_remain_available_from_sql_functions_public_surface():
    from app.db.sql_functions import translate_calculated_column

    expression = {"function": "UPPER", "params": {"column": "name"}}

    assert (
        translate_calculated_column("postgres", "upper_name", expression, table_alias="t")
        == "UPPER(t.name) AS upper_name"
    )


def test_string_function_translators_live_outside_sql_functions_module():
    import app.db.sql_functions as sql_functions
    import app.db.sql_string_functions as sql_string_functions

    assert issubclass(
        sql_functions.SqlFunctionTranslator,
        sql_string_functions.SqlStringFunctionMixin,
    )

    sql_functions_source = Path(sql_functions.__file__).read_text(encoding="utf-8")
    split_source = Path(sql_string_functions.__file__).read_text(encoding="utf-8")

    for method_name in (
        "upper",
        "lower",
        "trim",
        "concat",
        "substring",
        "replace",
        "left",
        "right",
        "position",
        "ltrim",
        "rtrim",
    ):
        definition = f"def _translate_{method_name}"
        assert definition not in sql_functions_source
        assert definition in split_source
