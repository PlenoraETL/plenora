"""Behavioral unit tests for TransformSchemaHandlers.

Esercita gli handler di schema implementati DIRETTAMENTE in
``app.core.projects.schema_transform_handlers`` (gli handler delegati ai
sotto-moduli geo/join/reshape/table/formula sono coperti dai loro file).

Lo schema e' simulato: nessuna esecuzione dataframe, solo trasformazioni di
liste ``ColumnSchema``. Costruiamo input minimi e verifichiamo i rami:
happy-path, input vuoto, varianti di config, no-op deterministici, type-shift.
"""

from __future__ import annotations

import pytest

from app.shared.execution.schema_types import ColumnSchema
from app.core.projects.schema_transform_handlers import TransformSchemaHandlers


pytestmark = [pytest.mark.unit]


NODE = "node-X"


@pytest.fixture
def handlers() -> TransformSchemaHandlers:
    return TransformSchemaHandlers(ColumnSchema)


def col(name: str, type_: str = "str", **kw) -> ColumnSchema:
    return ColumnSchema(name=name, type=type_, **kw)


def schema(*cols: ColumnSchema):
    """Wrap a single-input schema list as input_schemas[0]."""
    return [list(cols)]


def names(result):
    return [c.name for c in result]


def by_name(result, name):
    return next(c for c in result if c.name == name)


# --------------------------------------------------------------------------
# Construction / composition wiring
# --------------------------------------------------------------------------

def test_init_configures_composed_handlers(handlers):
    # Tutti gli handler composti devono ricevere gli helper condivisi.
    for h in (
        handlers._join_handlers,
        handlers._geo_handlers,
        handlers._reshape_handlers,
        handlers._formula_handlers,
        handlers._table_handlers,
    ):
        assert h._column_schema_cls is ColumnSchema
        assert h._copy_column == handlers._copy_column
        assert h._copy_schema == handlers._copy_schema
        assert h._normalize_str_list == handlers._normalize_str_list
        assert h._schema_passthrough == handlers._schema_passthrough


def test_build_transform_handlers_returns_known_ops(handlers):
    registry = handlers.build_transform_handlers({})
    # Un campione rappresentativo di op che devono essere presenti.
    for op in ("join", "filter", "rename", "drop", "drop_columns",
               "md5_hash", "type_cast", "bin", "geo_overlay", "melt"):
        assert op in registry
        assert callable(registry[op])


def test_registry_passthrough_op_keeps_schema(handlers):
    registry = handlers.build_transform_handlers({})
    inp = schema(col("a", "int"), col("b"))
    out = registry["fill_na"](inp, {}, NODE)
    assert names(out) == ["a", "b"]
    assert by_name(out, "a").source_node == NODE


def test_registry_filter_ignores_config(handlers):
    registry = handlers.build_transform_handlers({})
    inp = schema(col("a"))
    out = registry["filter"](inp, {"anything": True}, NODE)
    assert names(out) == ["a"]


def test_registry_aggregate_uses_trimmed_output_alias(handlers):
    registry = handlers.build_transform_handlers({})
    inp = schema(col("categoria"), col("importo", "real"))
    out = registry["aggregate"](
        inp,
        {
            "group_by": ["categoria"],
            "aggregations": [
                {"column": "importo", "function": "sum", "alias": " aggregazioni "},
            ],
        },
        NODE,
    )
    assert names(out) == ["categoria", "aggregazioni"]


def test_registry_aggregate_without_group_by_is_runtime_noop(handlers):
    registry = handlers.build_transform_handlers({})
    inp = schema(col("categoria"), col("importo", "real"))

    out = registry["aggregate"](inp, {"aggregations": []}, NODE)

    assert names(out) == ["categoria", "importo"]


def test_registry_aggregate_without_valid_aggregations_adds_count(handlers):
    registry = handlers.build_transform_handlers({})
    inp = schema(col("categoria"), col("importo", "real"))

    out = registry["aggregate"](
        inp,
        {"group_by": ["categoria"], "aggregations": []},
        NODE,
    )

    assert names(out) == ["categoria", "count"]
    assert by_name(out, "count").type == "int"


def test_registry_aggregate_multiple_functions_use_runtime_flat_names(handlers):
    registry = handlers.build_transform_handlers({})
    inp = schema(col("categoria"), col("importo", "int"))

    out = registry["aggregate"](
        inp,
        {
            "group_by": ["categoria"],
            "aggregations": [
                {"column": "importo", "function": "sum"},
                {"column": "importo", "function": "mean"},
            ],
        },
        NODE,
    )

    assert names(out) == ["categoria", "importo_sum", "importo_mean"]
    assert by_name(out, "importo_sum").type == "int"
    assert by_name(out, "importo_mean").type == "real"


def test_registry_pivot_requires_runtime_columns_and_explicit_mapping(handlers):
    registry = handlers.build_transform_handlers({})
    inp = schema(col("azienda"), col("anno", "int"), col("tipo"), col("valore", "real"))

    incomplete = registry["pivot"](inp, {"index_col": "azienda"}, NODE)
    assert names(incomplete) == ["azienda", "anno", "tipo", "valore"]

    out = registry["pivot"](
        inp,
        {
            "index_col": "azienda, anno",
            "pivot_col": "tipo",
            "value_col": "valore",
            "mapping": {"A": "valore_a", "B": "valore_b"},
        },
        NODE,
    )
    assert names(out) == ["azienda", "anno", "valore_a", "valore_b", "tipo", "valore"]
    assert [c.name for c in out if not c.optional] == ["azienda", "anno"]
    assert by_name(out, "valore_a").type == "real"


def test_registry_drop_via_dispatch(handlers):
    registry = handlers.build_transform_handlers({})
    inp = schema(col("a"), col("b"), col("c"))
    out = registry["drop"](inp, {"columns": ["b"]}, NODE)
    assert names(out) == ["a", "c"]


def test_registry_geo_overlay_reads_how(handlers):
    # geo_overlay legge config["how"]; deve almeno non sollevare e tornare lista.
    registry = handlers.build_transform_handlers({})
    inp = schema(col("geometry", "geometry"))
    out = registry["geo_overlay"](inp, {"how": "union"}, NODE)
    assert isinstance(out, list)


# --------------------------------------------------------------------------
# schema_passthrough (public + private)
# --------------------------------------------------------------------------

def test_schema_passthrough_public_copies_with_node(handlers):
    inp = schema(col("a", "int", id="id1", source_name="orig"))
    out = handlers.schema_passthrough(inp, NODE)
    assert names(out) == ["a"]
    c = out[0]
    assert c.type == "int"
    assert c.id == "id1"
    assert c.source_name == "orig"
    assert c.source_node == NODE


def test_schema_passthrough_empty_returns_empty(handlers):
    assert handlers.schema_passthrough([], NODE) == []


# --------------------------------------------------------------------------
# _schema_filter / _schema_sort
# --------------------------------------------------------------------------

def test_schema_filter_passthrough(handlers):
    inp = schema(col("a"), col("b", "int"))
    out = handlers._schema_filter(inp, NODE)
    assert names(out) == ["a", "b"]


def test_schema_sort_passthrough(handlers):
    inp = schema(col("a"), col("b"))
    out = handlers._schema_sort(inp, NODE)
    assert names(out) == ["a", "b"]


def test_schema_filter_empty(handlers):
    assert handlers._schema_filter([], NODE) == []


# --------------------------------------------------------------------------
# _schema_rename — tre formati di config
# --------------------------------------------------------------------------

def test_rename_columns_map(handlers):
    inp = schema(col("a"), col("b"), col("c"))
    out = handlers._schema_rename(inp, {"columns": {"a": "x", "c": "z"}}, NODE)
    assert names(out) == ["x", "b", "z"]


def test_rename_renames_list(handlers):
    inp = schema(col("a"), col("b"))
    cfg = {"renames": [
        {"old_name": "a", "new_name": "alpha"},
        {"old_name": "", "new_name": "skip"},   # filtrato
        {"bad": "shape"},                         # filtrato
    ]}
    out = handlers._schema_rename(inp, cfg, NODE)
    assert names(out) == ["alpha", "b"]


def test_rename_flat_old_new(handlers):
    inp = schema(col("a"), col("b"))
    out = handlers._schema_rename(inp, {"old_name": "b", "new_name": "beta"}, NODE)
    assert names(out) == ["a", "beta"]


def test_rename_no_map_passthrough_names(handlers):
    inp = schema(col("a"), col("b"))
    out = handlers._schema_rename(inp, {}, NODE)
    assert names(out) == ["a", "b"]


def test_rename_empty_input(handlers):
    assert handlers._schema_rename([], {"columns": {"a": "x"}}, NODE) == []


def test_rename_preserves_identity_metadata(handlers):
    inp = schema(col("a", "int", id="i1", source_name="src_a"))
    out = handlers._schema_rename(inp, {"columns": {"a": "renamed"}}, NODE)
    c = out[0]
    assert c.name == "renamed"
    assert c.type == "int"
    assert c.id == "i1"
    assert c.source_name == "src_a"
    assert c.source_node == NODE


# --------------------------------------------------------------------------
# _schema_drop
# --------------------------------------------------------------------------

def test_drop_removes_listed(handlers):
    inp = schema(col("a"), col("b"), col("c"))
    out = handlers._schema_drop(inp, {"columns": ["a", "c"]}, NODE)
    assert names(out) == ["b"]


def test_drop_no_columns_keeps_all(handlers):
    inp = schema(col("a"), col("b"))
    out = handlers._schema_drop(inp, {}, NODE)
    assert names(out) == ["a", "b"]


def test_drop_unknown_column_ignored(handlers):
    inp = schema(col("a"))
    out = handlers._schema_drop(inp, {"columns": ["zzz"]}, NODE)
    assert names(out) == ["a"]


def test_drop_empty_input(handlers):
    assert handlers._schema_drop([], {"columns": ["a"]}, NODE) == []


# --------------------------------------------------------------------------
# _schema_split_column
# --------------------------------------------------------------------------

def test_split_column_adds_new_str_columns(handlers):
    inp = schema(col("full", "str"))
    out = handlers._schema_split_column(inp, {"new_columns": ["first", "last"]}, NODE)
    assert names(out) == ["full", "first", "last"]
    assert by_name(out, "first").type == "str"
    assert by_name(out, "first").is_calculated is True


def test_split_column_skips_existing_and_empty(handlers):
    inp = schema(col("full"), col("first"))
    out = handlers._schema_split_column(inp, {"new_columns": ["first", "", "extra"]}, NODE)
    # "first" gia' esiste, "" ignorata, solo "extra" aggiunta
    assert names(out) == ["full", "first", "extra"]


def test_split_column_no_config(handlers):
    inp = schema(col("full"))
    out = handlers._schema_split_column(inp, {}, NODE)
    assert names(out) == ["full"]


def test_split_column_empty_input(handlers):
    assert handlers._schema_split_column([], {"new_columns": ["x"]}, NODE) == []


# --------------------------------------------------------------------------
# _schema_md5_hash
# --------------------------------------------------------------------------

def test_md5_hash_default_output(handlers):
    inp = schema(col("a"))
    out = handlers._schema_md5_hash(inp, {"columns": ["a"]}, NODE)
    assert names(out) == ["a", "md5_hash"]
    assert by_name(out, "md5_hash").type == "str"
    assert by_name(out, "md5_hash").is_calculated is True


def test_md5_hash_custom_output(handlers):
    inp = schema(col("a"))
    out = handlers._schema_md5_hash(inp, {"output_column": "digest", "columns": ["a"]}, NODE)
    assert names(out) == ["a", "digest"]


def test_md5_hash_empty_input(handlers):
    assert handlers._schema_md5_hash([], {}, NODE) == []


# --------------------------------------------------------------------------
# _schema_mask_data
# --------------------------------------------------------------------------

def test_mask_data_adds_masked_columns(handlers):
    inp = schema(col("email"), col("phone"))
    cfg = {"maskings": [{"column": "email"}, {"column": "phone"}]}
    out = handlers._schema_mask_data(inp, cfg, NODE)
    assert names(out) == ["email", "phone", "email_masked", "phone_masked"]


def test_mask_data_overwrite_no_new_columns(handlers):
    inp = schema(col("email"))
    cfg = {"overwrite": True, "maskings": [{"column": "email"}]}
    out = handlers._schema_mask_data(inp, cfg, NODE)
    assert names(out) == ["email"]


def test_mask_data_dedup_existing(handlers):
    inp = schema(col("email"), col("email_masked"))
    cfg = {"maskings": [{"column": "email"}]}
    out = handlers._schema_mask_data(inp, cfg, NODE)
    assert names(out) == ["email", "email_masked"]


def test_mask_data_empty_input(handlers):
    assert handlers._schema_mask_data([], {"maskings": []}, NODE) == []


# --------------------------------------------------------------------------
# _schema_type_cast
# --------------------------------------------------------------------------

@pytest.mark.parametrize("target,expected", [
    ("str", "str"),
    ("int", "int"),
    ("float", "real"),
    ("bool", "bool"),
    ("date", "date"),
    ("datetime", "datetime"),
    ("unknown_type", "str"),  # fallback
])
def test_type_cast_type_map(handlers, target, expected):
    inp = schema(col("a", "str"), col("b", "int"))
    out = handlers._schema_type_cast(inp, {"column": "a", "target_type": target}, NODE)
    assert by_name(out, "a").type == expected
    assert by_name(out, "a").is_calculated is True
    # le altre colonne non cambiano tipo
    assert by_name(out, "b").type == "int"


def test_type_cast_default_target_str(handlers):
    inp = schema(col("a", "int"))
    out = handlers._schema_type_cast(inp, {"column": "a"}, NODE)
    assert by_name(out, "a").type == "str"


def test_type_cast_unknown_column_noop(handlers):
    inp = schema(col("a", "int"))
    out = handlers._schema_type_cast(inp, {"column": "zzz", "target_type": "float"}, NODE)
    assert by_name(out, "a").type == "int"
    assert by_name(out, "a").is_calculated is False


def test_type_cast_empty_input(handlers):
    assert handlers._schema_type_cast([], {"column": "a"}, NODE) == []


# --------------------------------------------------------------------------
# _schema_text_normalize
# --------------------------------------------------------------------------

def test_text_normalize_overwrite_default_passthrough(handlers):
    inp = schema(col("a"), col("b"))
    out = handlers._schema_text_normalize(inp, {"columns": ["a"]}, NODE)
    # overwrite default True -> passthrough
    assert names(out) == ["a", "b"]


def test_text_normalize_no_overwrite_adds_norm(handlers):
    inp = schema(col("a"), col("b"))
    cfg = {"overwrite": False, "columns": ["a", "b"]}
    out = handlers._schema_text_normalize(inp, cfg, NODE)
    assert names(out) == ["a", "b", "a_norm", "b_norm"]
    assert by_name(out, "a_norm").type == "str"


def test_text_normalize_no_overwrite_dedup(handlers):
    inp = schema(col("a"), col("a_norm"))
    cfg = {"overwrite": False, "columns": ["a"]}
    out = handlers._schema_text_normalize(inp, cfg, NODE)
    assert names(out) == ["a", "a_norm"]


def test_text_normalize_empty_input(handlers):
    assert handlers._schema_text_normalize([], {"columns": ["a"]}, NODE) == []


# --------------------------------------------------------------------------
# _schema_add_row_number
# --------------------------------------------------------------------------

def test_add_row_number_default(handlers):
    inp = schema(col("a"))
    out = handlers._schema_add_row_number(inp, {}, NODE)
    assert names(out) == ["a", "row_number"]
    assert by_name(out, "row_number").type == "int"


def test_add_row_number_custom_output(handlers):
    inp = schema(col("a"))
    out = handlers._schema_add_row_number(inp, {"output_column": "rn"}, NODE)
    assert names(out) == ["a", "rn"]


def test_add_row_number_dedup(handlers):
    inp = schema(col("a"), col("row_number", "int"))
    out = handlers._schema_add_row_number(inp, {}, NODE)
    assert names(out) == ["a", "row_number"]


def test_add_row_number_empty_input(handlers):
    assert handlers._schema_add_row_number([], {}, NODE) == []


# --------------------------------------------------------------------------
# _schema_uuid_generator
# --------------------------------------------------------------------------

def test_uuid_generator_default(handlers):
    inp = schema(col("a"))
    out = handlers._schema_uuid_generator(inp, {}, NODE)
    assert names(out) == ["a", "uuid"]
    assert by_name(out, "uuid").type == "str"


def test_uuid_generator_custom(handlers):
    inp = schema(col("a"))
    out = handlers._schema_uuid_generator(inp, {"output_column": "guid"}, NODE)
    assert names(out) == ["a", "guid"]


def test_uuid_generator_empty_input(handlers):
    assert handlers._schema_uuid_generator([], {}, NODE) == []


# --------------------------------------------------------------------------
# _schema_string_length
# --------------------------------------------------------------------------

def test_string_length_default_name(handlers):
    inp = schema(col("name"))
    out = handlers._schema_string_length(inp, {"column": "name"}, NODE)
    assert names(out) == ["name", "name_length"]
    assert by_name(out, "name_length").type == "int"


def test_string_length_explicit_output(handlers):
    inp = schema(col("name"))
    out = handlers._schema_string_length(inp, {"column": "name", "output_column": "len"}, NODE)
    assert names(out) == ["name", "len"]


def test_string_length_no_column_no_output_noop(handlers):
    inp = schema(col("name"))
    out = handlers._schema_string_length(inp, {}, NODE)
    assert names(out) == ["name"]


def test_string_length_output_without_source_column_is_noop(handlers):
    inp = schema(col("name"))
    out = handlers._schema_string_length(inp, {"output_column": "len"}, NODE)
    assert names(out) == ["name"]


def test_string_length_dedup(handlers):
    inp = schema(col("name"), col("name_length", "int"))
    out = handlers._schema_string_length(inp, {"column": "name"}, NODE)
    assert names(out) == ["name", "name_length"]


def test_string_length_empty_input(handlers):
    assert handlers._schema_string_length([], {"column": "name"}, NODE) == []


# --------------------------------------------------------------------------
# _schema_date_extract
# --------------------------------------------------------------------------

def test_date_extract_default_year(handlers):
    inp = schema(col("ts", "datetime"))
    out = handlers._schema_date_extract(inp, {"column": "ts"}, NODE)
    assert names(out) == ["ts", "ts_year"]
    assert by_name(out, "ts_year").type == "int"


def test_date_extract_parts_list(handlers):
    inp = schema(col("ts", "datetime"))
    out = handlers._schema_date_extract(inp, {"column": "ts", "parts": ["year", "month", "day"]}, NODE)
    assert names(out) == ["ts", "ts_year", "ts_month", "ts_day"]


def test_date_extract_parts_string(handlers):
    inp = schema(col("ts", "datetime"))
    out = handlers._schema_date_extract(inp, {"column": "ts", "parts": "quarter"}, NODE)
    assert names(out) == ["ts"]


def test_date_extract_custom_prefix(handlers):
    inp = schema(col("ts", "datetime"))
    out = handlers._schema_date_extract(inp, {"column": "ts", "parts": ["year"], "prefix": "p_"}, NODE)
    assert names(out) == ["ts", "p_year"]


def test_date_extract_invalid_parts_ignored(handlers):
    inp = schema(col("ts", "datetime"))
    out = handlers._schema_date_extract(inp, {"column": "ts", "parts": ["year", "bogus", "month"]}, NODE)
    assert names(out) == ["ts", "ts_year", "ts_month"]


def test_date_extract_case_and_whitespace(handlers):
    inp = schema(col("ts", "datetime"))
    out = handlers._schema_date_extract(inp, {"column": "ts", "parts": [" YEAR "]}, NODE)
    assert names(out) == ["ts", "ts_year"]


def test_date_extract_no_column_noop(handlers):
    inp = schema(col("ts", "datetime"))
    out = handlers._schema_date_extract(inp, {"parts": ["year"]}, NODE)
    assert names(out) == ["ts"]


def test_date_extract_parts_none_defaults_year(handlers):
    inp = schema(col("ts", "datetime"))
    out = handlers._schema_date_extract(inp, {"column": "ts", "parts": None}, NODE)
    assert names(out) == ["ts", "ts_year"]


def test_date_extract_parts_invalid_type_defaults_year(handlers):
    inp = schema(col("ts", "datetime"))
    out = handlers._schema_date_extract(inp, {"column": "ts", "parts": 123}, NODE)
    assert names(out) == ["ts", "ts_year"]


def test_date_extract_dedup_duplicate_parts(handlers):
    inp = schema(col("ts", "datetime"))
    out = handlers._schema_date_extract(inp, {"column": "ts", "parts": ["year", "year"]}, NODE)
    assert names(out) == ["ts", "ts_year"]


def test_date_extract_empty_input(handlers):
    assert handlers._schema_date_extract([], {"column": "ts"}, NODE) == []


# --------------------------------------------------------------------------
# _schema_string_extract
# --------------------------------------------------------------------------

def test_string_extract_named_groups_ordered(handlers):
    inp = schema(col("addr"))
    cfg = {"column": "addr", "pattern": r"(?P<city>\w+),\s*(?P<zip>\d+)"}
    out = handlers._schema_string_extract(inp, cfg, NODE)
    assert names(out) == ["addr", "city", "zip"]
    assert by_name(out, "city").type == "str"


def test_string_extract_unnamed_default_output(handlers):
    inp = schema(col("addr"))
    out = handlers._schema_string_extract(inp, {"column": "addr", "pattern": r"(\d+)"}, NODE)
    assert names(out) == ["addr", "addr_extracted"]


def test_string_extract_unnamed_custom_output(handlers):
    inp = schema(col("addr"))
    cfg = {"column": "addr", "pattern": r"(\d+)", "output_column": "nums"}
    out = handlers._schema_string_extract(inp, cfg, NODE)
    assert names(out) == ["addr", "nums"]


def test_string_extract_missing_column_or_pattern_noop(handlers):
    inp = schema(col("addr"))
    assert names(handlers._schema_string_extract(inp, {"pattern": r"(\d+)"}, NODE)) == ["addr"]
    assert names(handlers._schema_string_extract(inp, {"column": "addr"}, NODE)) == ["addr"]


def test_string_extract_column_not_in_input_noop(handlers):
    inp = schema(col("addr"))
    out = handlers._schema_string_extract(inp, {"column": "zzz", "pattern": r"(\d+)"}, NODE)
    assert names(out) == ["addr"]


def test_string_extract_invalid_regex_noop(handlers):
    inp = schema(col("addr"))
    out = handlers._schema_string_extract(inp, {"column": "addr", "pattern": r"([unclosed"}, NODE)
    assert names(out) == ["addr"]


def test_string_extract_named_group_dedup(handlers):
    inp = schema(col("addr"), col("city"))
    cfg = {"column": "addr", "pattern": r"(?P<city>\w+)"}
    out = handlers._schema_string_extract(inp, cfg, NODE)
    assert names(out) == ["addr", "city"]


def test_string_extract_empty_input(handlers):
    assert handlers._schema_string_extract([], {"column": "a", "pattern": "x"}, NODE) == []


# --------------------------------------------------------------------------
# _schema_bin
# --------------------------------------------------------------------------

def test_bin_default_output(handlers):
    inp = schema(col("score", "int"))
    out = handlers._schema_bin(inp, {"column": "score"}, NODE)
    assert names(out) == ["score", "score_bin"]
    assert by_name(out, "score_bin").type == "str"


def test_bin_custom_output(handlers):
    inp = schema(col("score", "int"))
    out = handlers._schema_bin(inp, {"column": "score", "output_column": "band"}, NODE)
    assert names(out) == ["score", "band"]


def test_bin_no_column_noop(handlers):
    inp = schema(col("score", "int"))
    assert names(handlers._schema_bin(inp, {}, NODE)) == ["score"]


def test_bin_column_not_in_input_noop(handlers):
    inp = schema(col("score", "int"))
    assert names(handlers._schema_bin(inp, {"column": "zzz"}, NODE)) == ["score"]


def test_bin_dedup(handlers):
    inp = schema(col("score", "int"), col("score_bin"))
    assert names(handlers._schema_bin(inp, {"column": "score"}, NODE)) == ["score", "score_bin"]


def test_bin_empty_input(handlers):
    assert handlers._schema_bin([], {"column": "score"}, NODE) == []


# --------------------------------------------------------------------------
# _schema_string_pad
# --------------------------------------------------------------------------

def test_string_pad_overwrite_source(handlers):
    inp = schema(col("code", "int"), col("other"))
    out = handlers._schema_string_pad(inp, {"column": "code"}, NODE)
    assert names(out) == ["code", "other"]
    assert by_name(out, "code").type == "str"
    assert by_name(out, "code").is_calculated is True
    assert by_name(out, "other").type == "str"  # original type unchanged-ish


def test_string_pad_same_output_as_column(handlers):
    inp = schema(col("code", "int"))
    out = handlers._schema_string_pad(inp, {"column": "code", "output_column": "code"}, NODE)
    assert names(out) == ["code"]
    assert by_name(out, "code").type == "str"


def test_string_pad_new_output_column(handlers):
    inp = schema(col("code", "int"))
    out = handlers._schema_string_pad(inp, {"column": "code", "output_column": "padded"}, NODE)
    assert names(out) == ["code", "padded"]
    assert by_name(out, "code").type == "int"  # source preserved
    assert by_name(out, "padded").type == "str"


def test_string_pad_output_collides_existing(handlers):
    inp = schema(col("code", "int"), col("dest", "float"))
    out = handlers._schema_string_pad(inp, {"column": "code", "output_column": "dest"}, NODE)
    assert names(out) == ["code", "dest"]
    assert by_name(out, "code").type == "int"
    assert by_name(out, "dest").type == "str"  # overwritten to str


def test_string_pad_no_column_noop(handlers):
    inp = schema(col("code", "int"))
    assert names(handlers._schema_string_pad(inp, {}, NODE)) == ["code"]


def test_string_pad_column_not_in_input_noop(handlers):
    inp = schema(col("code", "int"))
    assert names(handlers._schema_string_pad(inp, {"column": "zzz"}, NODE)) == ["code"]


def test_string_pad_empty_input(handlers):
    assert handlers._schema_string_pad([], {"column": "code"}, NODE) == []


# --------------------------------------------------------------------------
# _normalize_str_list
# --------------------------------------------------------------------------

def test_normalize_str_list_none(handlers):
    assert handlers._normalize_str_list(None) == []


def test_normalize_str_list_csv_string(handlers):
    assert handlers._normalize_str_list("a, b ,, c") == ["a", "b", "c"]


def test_normalize_str_list_list(handlers):
    assert handlers._normalize_str_list(["a", " b ", "", 3]) == ["a", "b", "3"]


def test_normalize_str_list_other_type(handlers):
    assert handlers._normalize_str_list(42) == []


def test_normalize_str_list_empty_string(handlers):
    assert handlers._normalize_str_list("") == []


# --------------------------------------------------------------------------
# _copy_column / _copy_schema helpers
# --------------------------------------------------------------------------

def test_copy_column_preserves_metadata(handlers):
    c = col("a", "int", id="i", source_name="s", is_calculated=True)
    out = handlers._copy_column(c, NODE)
    assert out.name == "a"
    assert out.type == "int"
    assert out.id == "i"
    assert out.source_name == "s"
    assert out.source_node == NODE
    assert out.is_calculated is True


def test_copy_column_rename_and_type_override(handlers):
    c = col("a", "int")
    out = handlers._copy_column(c, NODE, name="b", type_="real", is_calculated=True)
    assert out.name == "b"
    assert out.type == "real"
    assert out.is_calculated is True
    # source_name falls back to original col name
    assert out.source_name == "a"


def test_copy_column_source_name_fallback(handlers):
    c = col("a")  # no source_name
    out = handlers._copy_column(c, NODE)
    assert out.source_name == "a"


def test_copy_schema_updates_node(handlers):
    inp = [col("a"), col("b", "int")]
    out = handlers._copy_schema(inp, NODE)
    assert names(out) == ["a", "b"]
    assert all(c.source_node == NODE for c in out)


# --------------------------------------------------------------------------
# _schema_join delegation (lightweight — full coverage in join handler tests)
# --------------------------------------------------------------------------

def test_schema_join_delegates_returns_list(handlers):
    # Senza edge configurati il join cade su un comportamento deterministico;
    # ci basta verificare delega + tipo di ritorno.
    inp = schema(col("a"))
    out = handlers._schema_join(inp, {}, NODE, {})
    assert isinstance(out, list)
