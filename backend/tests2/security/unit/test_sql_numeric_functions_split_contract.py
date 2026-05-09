from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


@pytest.mark.parametrize(
    "db,function_name,params,expected",
    [
        ("postgres", "ROUND", {"column": "amount", "decimals": 2}, "ROUND(t.amount::numeric, 2)"),
        ("mssql", "CEIL", {"column": "amount"}, "CEILING(t.amount)"),
        ("sqlite", "MOD", {"column": "amount", "divisor": 3}, "(t.amount % NULLIF(3.0, 0))"),
        ("mysql", "MATH", {"column1": "a", "operator": "/", "value": 2}, "IF(2.0 = 0, NULL, t.a / 2.0)"),
        ("postgres", "POWER", {"column": "amount", "exponent": 2}, "POWER(t.amount, 2.0)"),
        ("postgres", "SQRT", {"column": "amount"}, "SQRT(t.amount)"),
        ("postgres", "SIGN", {"column": "amount"}, "SIGN(t.amount)"),
        ("sqlite", "GREATEST", {"values": "a,b"}, "MAX(t.a, t.b)"),
        ("sqlite", "LEAST", {"values": ["a", "b"]}, "MIN(t.a, t.b)"),
    ],
)
def test_numeric_function_translation_stays_compatible(
    db,
    function_name,
    params,
    expected,
):
    from app.db.sql_functions import get_translator

    translator = get_translator(db)

    assert translator.translate(function_name, params, table_alias="t") == expected


def test_numeric_functions_remain_available_from_sql_functions_public_surface():
    from app.db.sql_functions import translate_calculated_column

    expression = {"function": "ABS", "params": {"column": "amount"}}

    assert (
        translate_calculated_column("postgres", "abs_amount", expression, table_alias="t")
        == "ABS(t.amount) AS abs_amount"
    )


def test_numeric_function_translators_live_outside_sql_functions_module():
    import app.db.sql_functions as sql_functions
    import app.db.sql_numeric_functions as sql_numeric_functions

    assert issubclass(
        sql_functions.SqlFunctionTranslator,
        sql_numeric_functions.SqlNumericFunctionMixin,
    )

    sql_functions_source = Path(sql_functions.__file__).read_text(encoding="utf-8")
    split_source = Path(sql_numeric_functions.__file__).read_text(encoding="utf-8")

    for method_name in (
        "round",
        "abs",
        "ceil",
        "floor",
        "math",
        "mod",
        "power",
        "sqrt",
        "sign",
        "greatest",
        "least",
    ):
        definition = f"def _translate_{method_name}"
        assert definition not in sql_functions_source
        assert definition in split_source


@pytest.mark.parametrize(
    "function_name,params",
    [
        ("MATH", {"column1": "a", "operator": "+", "value": "1); DROP TABLE x;--"}),
        ("MOD", {"column": "a", "divisor": "3); DROP TABLE x;--"}),
        ("POWER", {"column": "a", "exponent": "2); DROP TABLE x;--"}),
    ],
)
def test_numeric_split_keeps_injection_guards(function_name, params):
    from app.db.sql_functions import get_translator

    translator = get_translator("postgres")

    with pytest.raises(ValueError):
        translator.translate(function_name, params, table_alias="t")
