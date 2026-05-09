from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


@pytest.mark.parametrize(
    "db,function_name,params,expected",
    [
        ("postgres", "YEAR", {"column": "created_at"}, "EXTRACT(YEAR FROM t.created_at)"),
        ("sqlite", "MONTH", {"column": "created_at"}, "CAST(STRFTIME('%m', t.created_at) AS INTEGER)"),
        ("oracle", "NOW", {}, "SYSDATE"),
        ("mssql", "CURRENT_DATE", {}, "CAST(GETDATE() AS DATE)"),
        (
            "mysql",
            "DATE_DIFF",
            {"column": "end_at", "column2": "start_at"},
            "DATEDIFF(t.end_at, t.start_at)",
        ),
        (
            "postgres",
            "DATE_ADD",
            {"column": "created_at", "interval": 3, "unit": "day"},
            "t.created_at + INTERVAL '3 day'",
        ),
        (
            "mysql",
            "DATE_TRUNC",
            {"column": "created_at", "unit": "week"},
            "DATE(DATE_SUB(t.created_at, INTERVAL WEEKDAY(t.created_at) DAY))",
        ),
        (
            "sqlite",
            "DATE_FORMAT",
            {"column": "created_at", "format": "YYYY-MM-DD"},
            "STRFTIME('%Y-%m-%d', t.created_at)",
        ),
        (
            "mssql",
            "EXTRACT",
            {"column": "created_at", "part": "hour"},
            "DATEPART(hour, t.created_at)",
        ),
    ],
)
def test_date_function_translation_stays_compatible(
    db,
    function_name,
    params,
    expected,
):
    from app.db.sql_functions import get_translator

    translator = get_translator(db)

    assert translator.translate(function_name, params, table_alias="t") == expected


def test_date_functions_remain_available_from_sql_functions_public_surface():
    from app.db.sql_functions import translate_calculated_column

    expression = {"function": "YEAR", "params": {"column": "created_at"}}

    assert (
        translate_calculated_column("postgres", "created_year", expression, table_alias="t")
        == "EXTRACT(YEAR FROM t.created_at) AS created_year"
    )


def test_date_function_translators_live_outside_sql_functions_module():
    import app.db.sql_date_functions as sql_date_functions
    import app.db.sql_functions as sql_functions

    assert issubclass(
        sql_functions.SqlFunctionTranslator,
        sql_date_functions.SqlDateFunctionMixin,
    )

    sql_functions_source = Path(sql_functions.__file__).read_text(encoding="utf-8")
    split_source = Path(sql_date_functions.__file__).read_text(encoding="utf-8")

    for method_name in (
        "year",
        "month",
        "day",
        "now",
        "current_date",
        "date_diff",
        "date_add",
        "date_trunc",
        "date_format",
        "extract",
    ):
        definition = f"def _translate_{method_name}"
        assert definition not in sql_functions_source
        assert definition in split_source
