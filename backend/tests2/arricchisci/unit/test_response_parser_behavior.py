"""Behavioral coverage for the Arricchisci ResponseParser.

Exercises the public surface (ResponseParser.parse / get_preset_config) and the
internal navigation, extraction, transformation and condition branches that
shape the observable parsing behavior.
"""

from __future__ import annotations

import pytest

from app.core.arricchisci.response_parser import (
    PRESET_CONFIGS,
    ResponseParser,
    get_preset_config,
)


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci]


# ---------------------------------------------------------------------------
# Construction / defaults
# ---------------------------------------------------------------------------

def test_init_with_none_config_uses_empty_defaults():
    parser = ResponseParser(None)
    assert parser.config == {}
    assert parser.root_path == ""
    assert parser.iterate_on == []
    assert parser.mappings == []
    assert parser.transforms == []


def test_init_no_args_uses_empty_defaults():
    parser = ResponseParser()
    assert parser.root_path == ""
    assert parser.iterate_on == []


def test_init_reads_config_keys():
    config = {
        "root_path": "data",
        "iterate_on": [{"path": "", "as": "x"}],
        "mappings": [{"column": "c", "path": "x.v"}],
        "transforms": [{"column": "c2", "source": "c", "operation": "add", "value": 1}],
    }
    parser = ResponseParser(config)
    assert parser.root_path == "data"
    assert parser.iterate_on == config["iterate_on"]
    assert parser.mappings == config["mappings"]
    assert parser.transforms == config["transforms"]


# ---------------------------------------------------------------------------
# parse() top-level branches
# ---------------------------------------------------------------------------

def test_parse_root_path_not_found_returns_empty():
    parser = ResponseParser({"root_path": "missing"})
    assert parser.parse({"data": [1, 2]}) == []


def test_parse_simple_extraction_without_iterate_on_list():
    parser = ResponseParser(
        {
            "root_path": "items",
            "mappings": [
                {"column": "name", "path": "n"},
                {"column": "age", "path": "a", "default": 0},
            ],
        }
    )
    rows = parser.parse({"items": [{"n": "Alice", "a": 30}, {"n": "Bob"}]})
    assert rows == [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "age": 0},
    ]


def test_parse_simple_extraction_on_single_object_wraps_into_list():
    parser = ResponseParser(
        {
            "root_path": "obj",
            "mappings": [{"column": "name", "path": "n"}],
        }
    )
    rows = parser.parse({"obj": {"n": "single"}})
    assert rows == [{"name": "single"}]


def test_parse_simple_extraction_root_path_empty_uses_full_payload():
    parser = ResponseParser(
        {
            "root_path": "",
            "mappings": [{"column": "k", "path": "k"}],
        }
    )
    rows = parser.parse([{"k": 1}, {"k": 2}])
    assert rows == [{"k": 1}, {"k": 2}]


def test_parse_iterate_on_single_level():
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [{"path": "", "as": "row"}],
            "mappings": [{"column": "v", "path": "row.v"}],
        }
    )
    rows = parser.parse({"data": [{"v": 10}, {"v": 20}]})
    assert rows == [{"v": 10}, {"v": 20}]


def test_parse_nested_iterate_on_cartesian_expansion():
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [
                {"path": "", "as": "station"},
                {"path": "prod", "as": "product"},
            ],
            "mappings": [
                {"column": "station", "path": "station.name"},
                {"column": "var", "path": "product.var"},
            ],
        }
    )
    rows = parser.parse(
        {
            "data": [
                {"name": "A", "prod": [{"var": "x"}, {"var": "y"}]},
                {"name": "B", "prod": [{"var": "z"}]},
            ]
        }
    )
    assert rows == [
        {"station": "A", "var": "x"},
        {"station": "A", "var": "y"},
        {"station": "B", "var": "z"},
    ]


def test_parse_iterate_on_normalizes_non_list_into_single_element():
    # "prod" is a dict, not a list -> normalized to [dict]
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [
                {"path": "", "as": "station"},
                {"path": "prod", "as": "product"},
            ],
            "mappings": [{"column": "var", "path": "product.var"}],
        }
    )
    rows = parser.parse({"data": [{"prod": {"var": "single"}}]})
    assert rows == [{"var": "single"}]


def test_parse_iterate_on_missing_path_yields_no_rows():
    # "prod" missing -> _naviga_path returns None -> [] -> no rows for that branch
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [
                {"path": "", "as": "station"},
                {"path": "prod", "as": "product"},
            ],
            "mappings": [{"column": "var", "path": "product.var"}],
        }
    )
    rows = parser.parse({"data": [{"name": "no-prod"}]})
    assert rows == []


def test_parse_iterate_on_default_alias_when_as_missing():
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [{"path": ""}],  # no "as" -> level_0
            "mappings": [{"column": "v", "path": "level_0.v"}],
        }
    )
    rows = parser.parse({"data": [{"v": 99}]})
    assert rows == [{"v": 99}]


# ---------------------------------------------------------------------------
# _estrai_riga / mappings edge cases
# ---------------------------------------------------------------------------

def test_mapping_without_column_or_path_is_skipped():
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [{"path": "", "as": "row"}],
            "mappings": [
                {"column": "", "path": "row.v"},  # no column
                {"column": "c", "path": ""},  # no path
                {"column": "ok", "path": "row.v"},
            ],
        }
    )
    rows = parser.parse({"data": [{"v": 5}]})
    assert rows == [{"ok": 5}]


def test_mapping_default_applied_when_value_none():
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [{"path": "", "as": "row"}],
            "mappings": [{"column": "missing", "path": "row.nope", "default": "fallback"}],
        }
    )
    rows = parser.parse({"data": [{"v": 1}]})
    assert rows == [{"missing": "fallback"}]


def test_value_from_context_alias_not_present_falls_back_to_direct_path():
    # path "unknown.v" where "unknown" not in context -> tries direct nav on context dict
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [{"path": "", "as": "row"}],
            "mappings": [{"column": "c", "path": "row.row.v"}],
        }
    )
    # "row" alias -> object {"row": {"v": 7}} -> remaining "row.v" -> 7
    rows = parser.parse({"data": [{"row": {"v": 7}}]})
    assert rows == [{"c": 7}]


def test_value_from_context_alias_only_returns_whole_object():
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [{"path": "", "as": "row"}],
            "mappings": [{"column": "whole", "path": "row"}],
        }
    )
    rows = parser.parse({"data": [{"a": 1}]})
    assert rows == [{"whole": {"a": 1}}]


# ---------------------------------------------------------------------------
# _naviga_path navigation features (exercised via simple extraction)
# ---------------------------------------------------------------------------

def _nav(data, path, **config):
    parser = ResponseParser(config)
    return parser._naviga_path(data, path)


def test_naviga_path_empty_returns_data():
    assert _nav({"a": 1}, "") == {"a": 1}


def test_naviga_path_none_data_returns_none():
    assert _nav(None, "a.b") is None


def test_naviga_path_dot_notation():
    assert _nav({"a": {"b": {"c": 42}}}, "a.b.c") == 42


def test_naviga_path_intermediate_none_short_circuits():
    assert _nav({"a": None}, "a.b.c") is None


def test_naviga_path_positive_index():
    assert _nav({"arr": [10, 20, 30]}, "arr[1]") == 20


def test_naviga_path_positive_index_out_of_range():
    assert _nav({"arr": [10]}, "arr[5]") is None


def test_naviga_path_negative_index():
    assert _nav({"arr": [10, 20, 30]}, "arr[-1]") == 30


def test_naviga_path_negative_index_out_of_range():
    assert _nav({"arr": [10]}, "arr[-3]") is None


def test_naviga_path_filter_match():
    data = {"details": [{"var": "A", "val": 1}, {"var": "B", "val": 2}]}
    assert _nav(data, "details[var=B]") == {"var": "B", "val": 2}


def test_naviga_path_filter_no_match_returns_none():
    data = {"details": [{"var": "A"}]}
    assert _nav(data, "details[var=Z]") is None


def test_naviga_path_filter_then_field():
    data = {"details": [{"var": "B", "val": {"x": 9}}]}
    assert _nav(data, "details[var=B].val.x") == 9


def test_naviga_path_bracket_on_missing_field_returns_none():
    assert _nav({"a": 1}, "missing[0]") is None


def test_naviga_path_bracket_without_field_name_on_list():
    assert _nav([10, 20], "[1]") == 20


def test_naviga_path_field_not_found_returns_none():
    assert _nav({"a": 1}, "b") is None


def test_applica_bracket_on_non_list_returns_none():
    parser = ResponseParser({})
    assert parser._applica_bracket({"not": "list"}, "0") is None


def test_applica_bracket_invalid_content_returns_none():
    parser = ResponseParser({})
    assert parser._applica_bracket([1, 2, 3], "notanindex") is None


def test_ottieni_campo_from_object_attribute():
    class Obj:
        def __init__(self):
            self.foo = "bar"

    parser = ResponseParser({})
    assert parser._ottieni_campo(Obj(), "foo") == "bar"


def test_ottieni_campo_missing_returns_none():
    parser = ResponseParser({})
    assert parser._ottieni_campo(123, "anything") is None


# ---------------------------------------------------------------------------
# _splitta_path
# ---------------------------------------------------------------------------

def test_splitta_path_preserves_brackets():
    parser = ResponseParser({})
    assert parser._splitta_path("stat.details[0].val") == ["stat", "details[0]", "val"]


def test_splitta_path_dot_inside_bracket_is_preserved():
    parser = ResponseParser({})
    assert parser._splitta_path("a[x=y.z].b") == ["a[x=y.z]", "b"]


def test_splitta_path_single_segment():
    parser = ResponseParser({})
    assert parser._splitta_path("only") == ["only"]


# ---------------------------------------------------------------------------
# Transforms: _applica_operazione branches
# ---------------------------------------------------------------------------

def _transform_value(operation, source_value, value=None):
    parser = ResponseParser({})
    return parser._applica_operazione(source_value, operation, value)


def test_op_subtract():
    assert _transform_value("subtract", 10, 3) == 7.0


def test_op_add():
    assert _transform_value("add", 10, 3) == 13.0


def test_op_multiply():
    assert _transform_value("multiply", 4, 3) == 12.0


def test_op_divide():
    assert _transform_value("divide", 10, 4) == 2.5


def test_op_divide_by_zero_returns_none():
    assert _transform_value("divide", 10, 0) is None


def test_op_round_with_decimals():
    assert _transform_value("round", 3.14159, 2) == 3.14


def test_op_round_default_zero_decimals():
    assert _transform_value("round", 3.7, None) == 4


def test_op_kelvin_to_celsius():
    assert _transform_value("kelvin_to_celsius", 293.15) == 20.0


def test_op_celsius_to_kelvin():
    assert _transform_value("celsius_to_kelvin", 20) == 293.15


def test_op_uppercase():
    assert _transform_value("uppercase", "abc") == "ABC"


def test_op_lowercase():
    assert _transform_value("lowercase", "ABC") == "abc"


def test_op_prefix():
    assert _transform_value("prefix", "name", "pre_") == "pre_name"


def test_op_suffix():
    assert _transform_value("suffix", "name", "_suf") == "name_suf"


def test_op_replace_with_dict():
    assert _transform_value("replace", "a-b-c", {"find": "-", "replace": "_"}) == "a_b_c"


def test_op_replace_non_dict_value_returns_none():
    # replace branch with non-dict value falls through without return -> None
    assert _transform_value("replace", "abc", "notadict") is None


def test_op_default_if_null_keeps_value():
    assert _transform_value("default_if_null", "present", "fallback") == "present"


def test_op_unknown_returns_source_value():
    assert _transform_value("nonexistent_op", "untouched", 1) == "untouched"


def test_op_value_error_returns_source_value():
    # float("notnum") raises ValueError -> caught -> returns source value
    assert _transform_value("subtract", "notnum", 3) == "notnum"


# ---------------------------------------------------------------------------
# Transforms applied through parse() / _applica_trasformazioni
# ---------------------------------------------------------------------------

def test_transform_applied_when_condition_met():
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [{"path": "", "as": "row"}],
            "mappings": [
                {"column": "var", "path": "row.var"},
                {"column": "temp", "path": "row.temp"},
            ],
            "transforms": [
                {
                    "column": "celsius",
                    "source": "temp",
                    "operation": "kelvin_to_celsius",
                    "condition": "var == 'B12101'",
                }
            ],
        }
    )
    rows = parser.parse({"data": [{"var": "B12101", "temp": 300.15}]})
    assert rows == [{"var": "B12101", "temp": 300.15, "celsius": 27.0}]


def test_transform_skipped_when_condition_not_met():
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [{"path": "", "as": "row"}],
            "mappings": [
                {"column": "var", "path": "row.var"},
                {"column": "temp", "path": "row.temp"},
            ],
            "transforms": [
                {
                    "column": "celsius",
                    "source": "temp",
                    "operation": "kelvin_to_celsius",
                    "condition": "var == 'B12101'",
                }
            ],
        }
    )
    rows = parser.parse({"data": [{"var": "OTHER", "temp": 300.15}]})
    assert rows == [{"var": "OTHER", "temp": 300.15}]
    assert "celsius" not in rows[0]


def test_transform_source_none_sets_column_none():
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [{"path": "", "as": "row"}],
            "mappings": [{"column": "var", "path": "row.var"}],
            "transforms": [
                {"column": "derived", "source": "absent", "operation": "uppercase"}
            ],
        }
    )
    rows = parser.parse({"data": [{"var": "x"}]})
    assert rows == [{"var": "x", "derived": None}]


def test_transform_without_column_is_skipped():
    parser = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [{"path": "", "as": "row"}],
            "mappings": [{"column": "v", "path": "row.v"}],
            "transforms": [{"column": "", "source": "v", "operation": "uppercase"}],
        }
    )
    rows = parser.parse({"data": [{"v": "a"}]})
    assert rows == [{"v": "a"}]


def test_no_transforms_returns_rows_unchanged():
    parser = ResponseParser({})
    rows = [{"a": 1}]
    assert parser._applica_trasformazioni(rows) is rows


# ---------------------------------------------------------------------------
# _valuta_condizione
# ---------------------------------------------------------------------------

def test_condition_equals_true():
    parser = ResponseParser({})
    assert parser._valuta_condizione({"v": "B12101"}, "v == 'B12101'") is True


def test_condition_equals_false():
    parser = ResponseParser({})
    assert parser._valuta_condizione({"v": "OTHER"}, "v == 'B12101'") is False


def test_condition_not_equals_true():
    parser = ResponseParser({})
    assert parser._valuta_condizione({"v": "OTHER"}, "v != 'B12101'") is True


def test_condition_not_equals_false():
    parser = ResponseParser({})
    assert parser._valuta_condizione({"v": "B12101"}, "v != 'B12101'") is False


def test_condition_unparsable_returns_true():
    parser = ResponseParser({})
    assert parser._valuta_condizione({"v": "x"}, "this is not a condition !!!") is True


def test_condition_missing_column_uses_empty_string():
    parser = ResponseParser({})
    # column "v" missing -> "" compared with "B12101" -> not equal
    assert parser._valuta_condizione({}, "v == 'B12101'") is False


# ---------------------------------------------------------------------------
# get_preset_config / PRESET_CONFIGS
# ---------------------------------------------------------------------------

def test_get_preset_config_known_returns_copy():
    cfg = get_preset_config("simple_array")
    assert cfg == PRESET_CONFIGS["simple_array"]
    assert cfg is not PRESET_CONFIGS["simple_array"]


def test_get_preset_config_unknown_returns_empty():
    assert get_preset_config("does_not_exist") == {}


def test_get_preset_config_all_presets_present():
    for name in ("italiameteo_observations", "geojson_features", "simple_array"):
        assert get_preset_config(name) != {}


def test_italiameteo_preset_end_to_end():
    parser = ResponseParser(get_preset_config("italiameteo_observations"))
    rows = parser.parse(
        {
            "data": [
                {
                    "stat": {
                        "lat": 44.5,
                        "lon": 11.3,
                        "net": "RIRER",
                        "details": [{"val": "Bologna"}],
                    },
                    "prod": [
                        {
                            "var": "B12101",
                            "val": [{"val": 293.15, "ref": "2024-01-01T00:00"}],
                        }
                    ],
                }
            ]
        }
    )
    assert rows == [
        {
            "lat": 44.5,
            "lon": 11.3,
            "rete": "RIRER",
            "nome_stazione": "Bologna",
            "variabile": "B12101",
            "valore": 293.15,
            "timestamp": "2024-01-01T00:00",
            "valore_celsius": 20.0,
        }
    ]
