"""Contract test BE-SCHEMA-INFER-001A — `_infer_transform_schema`
parity vs `transforms_registry.json`.

Audit-only batch (lane D di BE-PHASE2-PARALLEL-PACK-001).

Verifica la parita' fra:
- l'if/elif chain in `SchemaInferenceService._infer_transform_schema`
  (oggi `app/services/schema_inference.py`, righe ~366-441), che
  mappa esplicitamente i `transform_op` ai metodi `_schema_xxx`;
- i transform IDs definiti in `app/core/manipola/transforms_registry.json`,
  fonte di verita' per le trasformazioni eseguibili (PluginExecutor).

Ogni transform_op presente nel registry ma NON nella dispatcher chain
cade nel fallback `_schema_passthrough` con warning. Per le transform
shape-preserving (sort, filter, distinct, sample) il passthrough e'
corretto. Per le transform che ALTERANO lo schema (es.
`add_row_number`, `geo_explode`, `geo_length`, `melt`, ecc.) il
passthrough produce uno schema sbagliato — il FE mostrera' colonne
disallineate dal runtime.

BE-SCHEMA-INFER-001A outcome (audit-only):
1. snapshot esatto delle 34 branch attive nel dispatcher;
2. snapshot esatto delle 70 transform IDs nel registry;
3. snapshot del gap (registry-only, dispatcher-only);
4. classificazione del gap in "passthrough-OK" vs "passthrough-WRONG".

Trigger di follow-up (BE-SCHEMA-INFER-002): ogni nuova entry nel gap
"passthrough-WRONG" richiede un metodo `_schema_xxx` dedicato +
estensione del dispatcher prima del rilascio della transform al FE.

Niente runtime change qui: solo test go-ready che fallisce
rumorosamente quando registry/dispatcher divergono.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Set

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent  # .../backend
_SCHEMA_INFER_PY = _BACKEND_ROOT / "app" / "services" / "schema_inference.py"
_REGISTRY_JSON = _BACKEND_ROOT / "app" / "core" / "manipola" / "transforms_registry.json"


# ---------------------------------------------------------------------------
# Source-bound parsing
# ---------------------------------------------------------------------------

_DISPATCH_OP_RE = re.compile(r'transform_op\s*==\s*"([a-zA-Z0-9_]+)"')


def _parse_dispatcher_branches() -> Set[str]:
    """Estrae le branch attive dal dispatcher in
    `_infer_transform_schema`. Source-bound: parsing del file sorgente,
    non del modulo importato — cosi' un futuro refactor (es. table
    dispatch) fa fallire il test e forza la review."""
    src = _SCHEMA_INFER_PY.read_text(encoding="utf-8")
    return set(_DISPATCH_OP_RE.findall(src))


def _load_registry_ids() -> Set[str]:
    data = json.loads(_REGISTRY_JSON.read_text(encoding="utf-8"))
    return set(data.get("transforms", {}).keys())


# ---------------------------------------------------------------------------
# Reference snapshots — failure here = registry/dispatcher drift
# ---------------------------------------------------------------------------

# Branch attive nel dispatcher al momento del lock BE-SCHEMA-INFER-001A.
# Aggiungere/rimuovere una branch aggiorna questo set + il classifier
# sotto.
EXPECTED_DISPATCHER_BRANCHES: Set[str] = {
    # base
    "join", "sjoin", "aggregate", "formula", "filter", "concat", "sort",
    "distinct", "rename", "drop", "drop_columns", "fill_na", "replace",
    "split_column",
    # geo
    "geo_buffer", "geo_centroid", "geo_area", "geo_clip", "geo_dissolve",
    "geo_simplify", "geo_convex_hull", "geo_reproject", "geo_intersection",
    "geo_union", "geo_difference", "geo_to_crs",
    # privacy
    "md5_hash", "mask_data",
    # additional base
    "type_cast", "conditional", "text_normalize",
    # passthrough explicits
    "concat_columns", "pivot", "geo_envelope",
    # spatial
    "geo_from_coords",
    # BE-PHASE2-SCHEMA-INFER-002 batch 1
    "add_row_number", "uuid_generator", "string_length", "date_extract",
    "geo_distance", "geo_to_wkt",
    # BE-PHASE2-SCHEMA-INFER-003 batch 2
    "geo_length", "geo_perimeter", "geo_vertex_count",
    "string_extract", "bin", "string_pad",
    # BE-PHASE2-SCHEMA-INFER-004 batch 3
    "geo_bounds_extractor", "geo_count_points_in_polygons",
    "geo_line_builder", "geo_polygon_builder",
    "geo_point_on_surface", "geo_boundary",
    # BE-PHASE2-SCHEMA-INFER-005 batch 4
    "reorder_columns", "geo_voronoi",
    # BE-PHASE2-SCHEMA-INFER-006 batch 5
    "cross_join", "geo_within", "geo_overlay",
    "geo_symmetric_difference", "geo_nearest", "lookup",
    # BE-PHASE2-SCHEMA-INFER-007 batch 6
    "table_diff", "melt", "statistics", "window_function",
}

# Transform IDs nel registry che ALTERANO lo schema (aggiungono/rimuovono
# colonne, cambiano tipi). Il passthrough produce schema FE sbagliato.
# Drift target: ogni voce qui serve di un metodo `_schema_xxx` dedicato
# in `_infer_transform_schema`.
PASSTHROUGH_WRONG_GAP: Set[str] = {
    # BE-PHASE2-SCHEMA-INFER-002 batch 1 ha promosso (rimossi):
    #   add_row_number, uuid_generator, string_length, date_extract,
    #   geo_distance, geo_to_wkt
    # BE-PHASE2-SCHEMA-INFER-003 batch 2 ha promosso (rimossi):
    #   geo_length, geo_perimeter, geo_vertex_count, string_extract,
    #   bin, string_pad
    # BE-PHASE2-SCHEMA-INFER-004 batch 3 ha promosso (rimossi):
    #   geo_bounds_extractor, geo_count_points_in_polygons,
    #   geo_line_builder, geo_polygon_builder, geo_point_on_surface,
    #   geo_boundary
    # BE-PHASE2-SCHEMA-INFER-005 batch 4 ha promosso a dispatcher
    # (rimossi): reorder_columns, geo_voronoi.
    # BE-PHASE2-SCHEMA-INFER-005 batch 4 ha riclassificato a
    # PASSTHROUGH_OK_GAP (rimossi): dedup_advanced, geo_clean_topology,
    # geo_make_valid, geo_explode.
    # BE-PHASE2-SCHEMA-INFER-006 batch 5 ha promosso a dispatcher
    # (rimossi): cross_join, geo_within, geo_overlay,
    # geo_symmetric_difference, geo_nearest, lookup.
    # BE-PHASE2-SCHEMA-INFER-007 batch 6 ha promosso a dispatcher
    # (rimossi): table_diff, melt, statistics, window_function.
    # I 2 residui sono `MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT`: lo
    # schema output dipende dai dati a runtime e non e' inferibile
    # staticamente da input schema + config. Restano in fallback
    # passthrough (schema announcement approssimato dal FE che vede
    # le sole colonne LEFT — il warning runtime documenta il caso).
    "flatten_json",         # MIGRATE_NEXT: keys derivate dai dati JSON parsati
    "transpose",            # MIGRATE_NEXT: cols derivate dai valori delle righe
}

# Transform IDs `MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT`: lo schema
# output dipende dai dati (non solo dalla config), quindi non puo'
# essere inferito al canvas-time. Annotati per drift-tracking.
MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT: Set[str] = {
    # `flatten_json`: pd.json_normalize sui valori parsati JSON. Le
    # chiavi del dict (e quindi i nomi colonna output) vengono dai
    # dati, non dalla config. Schema canvas-time impossibile.
    "flatten_json",
    # `transpose`: con `id_column` valorizzata, i valori della
    # colonna diventano i nomi delle nuove colonne. Senza
    # `id_column`, le colonne diventano `col_0`...`col_N` dove N
    # dipende dal row count. Entrambi i percorsi sono
    # data-dependent.
    "transpose",
}

# Transform IDs che operano solo su righe / valori senza alterare
# il column-set — il passthrough fallback e' funzionalmente corretto
# per il FE. Verifica runtime BE-PHASE2-SCHEMA-INFER-005:
# - `dedup_advanced`: drop_duplicates (rows-only).
# - `geo_clean_topology`: make_valid + buffer + drop empty (rows/values).
# - `geo_make_valid`: shapely.make_valid sui valori della colonna
#   geometria (column-set invariato).
# - `geo_explode`: explode(index_parts=False).reset_index(drop=True)
#   moltiplica le righe ma preserva il column-set.
# Tutti e 4 transitano in `_schema_passthrough` via fallback (nessun
# branch dispatcher dedicato necessario).
PASSTHROUGH_OK_GAP: Set[str] = {
    "sample",  # filter-like su righe
    "dedup_advanced",        # drop_duplicates rows-only
    "geo_clean_topology",    # make_valid + buffer + drop empty
    "geo_make_valid",        # shapely.make_valid sui valori
    "geo_explode",           # explode multi-geometrie, schema invariato
}


# ---------------------------------------------------------------------------
# 1. Branch dispatcher — snapshot lock
# ---------------------------------------------------------------------------


def test_dispatcher_branches_match_expected_snapshot():
    """Source-bound: l'if/elif chain reale in schema_inference.py
    deve combaciare con il set atteso. Drift in piu' = transform
    nuova non documentata; drift in meno = branch persa nel refactor.
    """
    actual = _parse_dispatcher_branches()
    assert actual == EXPECTED_DISPATCHER_BRANCHES, (
        f"Dispatcher drift in `_infer_transform_schema`. "
        f"Manca: {EXPECTED_DISPATCHER_BRANCHES - actual}. "
        f"In piu': {actual - EXPECTED_DISPATCHER_BRANCHES}. "
        f"Aggiorna `EXPECTED_DISPATCHER_BRANCHES` o ripara la chain."
    )


# ---------------------------------------------------------------------------
# 2. Registry — load + sanity
# ---------------------------------------------------------------------------


def test_registry_loads_and_has_known_size():
    """Sentinel sulla source-of-truth registry: se il registry cambia
    drasticamente vogliamo notarlo qui prima che si propaghi nei test
    di parity."""
    ids = _load_registry_ids()
    # Lock alla cardinalita' attuale (70). Aggiornabile insieme al gap.
    assert len(ids) == 70, (
        f"Registry size cambiata: ora {len(ids)} transforms. "
        f"Aggiorna `EXPECTED_DISPATCHER_BRANCHES` / `PASSTHROUGH_*_GAP` "
        f"e questo assert."
    )


# ---------------------------------------------------------------------------
# 3. Parity audit — registry vs dispatcher
# ---------------------------------------------------------------------------


def test_dispatcher_extras_are_aliases_only():
    """Le branch nel dispatcher che NON sono nel registry devono
    essere alias documentati. Oggi:
    - `drop` — alias storico per `drop_columns` (registry usa
      `drop_columns`);
    - `geo_to_crs` — alias legacy per `geo_reproject` (registry usa
      `geo_reproject`).
    Qualunque altra branch fuori-registry e' un transform_op
    fantasma."""
    dispatcher = _parse_dispatcher_branches()
    registry = _load_registry_ids()
    extras = dispatcher - registry
    assert extras == {"drop", "geo_to_crs"}, (
        f"Dispatcher branch fuori-registry inattese: {extras}. "
        f"Documentale come alias o rimuovile."
    )


def test_gap_classification_covers_all_registry_only_ids():
    """Ogni transform_op presente nel registry ma assente dal
    dispatcher deve essere classificato in PASSTHROUGH_WRONG_GAP
    (richiede `_schema_xxx` dedicato) o PASSTHROUGH_OK_GAP
    (rows-only, passthrough corretto). Lock vincolante: una nuova
    entry registry senza classificazione fallisce qui."""
    dispatcher = _parse_dispatcher_branches()
    registry = _load_registry_ids()
    registry_only = registry - dispatcher

    classified = PASSTHROUGH_WRONG_GAP | PASSTHROUGH_OK_GAP
    unclassified = registry_only - classified
    over_classified = classified - registry_only

    assert unclassified == set(), (
        f"Transform registry senza classificazione: {unclassified}. "
        f"Aggiungile a `PASSTHROUGH_WRONG_GAP` o `PASSTHROUGH_OK_GAP`."
    )
    assert over_classified == set(), (
        f"Classificazioni stale (transform non piu' nel registry o "
        f"gia' nel dispatcher): {over_classified}. Pulisci i set."
    )


def test_passthrough_wrong_gap_size_lock():
    """Lock dimensione del gap "passthrough-WRONG". Ogni progresso
    dovrebbe ridurre questo numero (transform_op promosso a metodo
    `_schema_xxx` dedicato in BE-SCHEMA-INFER-002+)."""
    actual = len(PASSTHROUGH_WRONG_GAP)
    # Lock attuale al BE-SCHEMA-INFER-001A baseline. Quando una
    # transform passa a dispatcher dedicato, sposta l'entry e
    # decrementa il limite (`assert actual <= N`).
    assert actual == 2, (
        f"Gap passthrough-WRONG cambiato: {actual}. "
        f"Aggiorna l'assert e il piano BE-SCHEMA-INFER-002+."
    )


# ---------------------------------------------------------------------------
# 4. Behavioral verification — fallback path
# ---------------------------------------------------------------------------


def test_dispatcher_falls_back_to_passthrough_for_unknown_ops():
    """Verifica end-to-end che un `transform_op` ancora non promosso
    (qui un'entry reale dal gap residuo) ricada nel passthrough con
    warning. Niente import pesanti — usiamo `SimpleNamespace` per il
    nodo."""
    from types import SimpleNamespace
    from app.services.schema_inference import (
        ColumnSchema,
        SchemaInferenceService,
    )

    service = SchemaInferenceService(project_service=None)
    input_schema = [
        ColumnSchema(name="a", type="int", source_node="src", is_calculated=False),
        ColumnSchema(name="b", type="str", source_node="src", is_calculated=False),
    ]
    node = SimpleNamespace(
        id="n1",
        config={},
        transform_op="flatten_json",  # MIGRATE_NEXT residual entry
        data=None,
    )
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    # Passthrough: stesso numero di colonne, stessi nomi/tipi.
    assert [(c.name, c.type) for c in out] == [(c.name, c.type) for c in input_schema]


def test_dispatcher_falls_back_for_unknown_phantom_op():
    """Op completamente sconosciuta (non nel registry, non nel
    dispatcher) deve comunque fare passthrough invece di crashare."""
    from types import SimpleNamespace
    from app.services.schema_inference import (
        ColumnSchema,
        SchemaInferenceService,
    )

    service = SchemaInferenceService(project_service=None)
    input_schema = [
        ColumnSchema(name="x", type="real", source_node="src", is_calculated=False),
    ]
    node = SimpleNamespace(
        id="n_phantom",
        config={},
        transform_op="op_che_non_esiste_da_nessuna_parte",
        data=None,
    )
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert len(out) == 1
    assert out[0].name == "x"
    assert out[0].type == "real"


# ---------------------------------------------------------------------------
# 5. Behavioral tests — BE-PHASE2-SCHEMA-INFER-002 batch 1
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from app.services.schema_inference import (  # noqa: E402
    ColumnSchema,
    SchemaInferenceService,
)


def _service_and_input():
    service = SchemaInferenceService(project_service=None)
    input_schema = [
        ColumnSchema(name="a", type="int", source_node="src", is_calculated=False),
        ColumnSchema(name="b", type="str", source_node="src", is_calculated=False),
    ]
    return service, input_schema


def _node(transform_op: str, config: dict) -> SimpleNamespace:
    return SimpleNamespace(id="n1", config=config, transform_op=transform_op, data=None)


# add_row_number ------------------------------------------------------------


def test_add_row_number_default_output_column():
    service, input_schema = _service_and_input()
    node = _node("add_row_number", {})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    names = [c.name for c in out]
    assert names == ["a", "b", "row_number"]
    assert out[-1].type == "int"
    assert out[-1].is_calculated is True


def test_add_row_number_custom_output_column():
    service, input_schema = _service_and_input()
    node = _node("add_row_number", {"output_column": "rn", "start": 100})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "rn"]
    assert out[-1].type == "int"


def test_add_row_number_collision_does_not_duplicate():
    """Se l'output column collide con una colonna esistente, non viene
    aggiunta una colonna duplicata. Allineato al comportamento runtime
    (overwrite) — qui lo schema non duplica il nome."""
    service, input_schema = _service_and_input()
    node = _node("add_row_number", {"output_column": "a"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    names = [c.name for c in out]
    assert names == ["a", "b"]


# uuid_generator ------------------------------------------------------------


def test_uuid_generator_default():
    service, input_schema = _service_and_input()
    node = _node("uuid_generator", {})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "uuid"]
    assert out[-1].type == "str"


def test_uuid_generator_custom_column():
    service, input_schema = _service_and_input()
    node = _node("uuid_generator", {"output_column": "rec_id"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "rec_id"]


# string_length -------------------------------------------------------------


def test_string_length_derives_default_output_column():
    """Senza `output_column`, il runtime usa `f"{column}_length"`."""
    service, input_schema = _service_and_input()
    node = _node("string_length", {"column": "b"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "b_length"]
    assert out[-1].type == "int"


def test_string_length_explicit_output_column():
    service, input_schema = _service_and_input()
    node = _node("string_length", {"column": "b", "output_column": "blen"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "blen"]


def test_string_length_no_column_passthrough():
    """Senza `column` ne' `output_column` → schema invariato."""
    service, input_schema = _service_and_input()
    node = _node("string_length", {})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b"]


# date_extract --------------------------------------------------------------


def test_date_extract_default_year_only():
    """Senza `parts`, runtime usa `["year"]`. Prefix derivato da `column`."""
    service = SchemaInferenceService(project_service=None)
    input_schema = [ColumnSchema(name="created_at", type="date", source_node="s", is_calculated=False)]
    node = _node("date_extract", {"column": "created_at"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["created_at", "created_at_year"]
    assert out[-1].type == "int"


def test_date_extract_multiple_parts_with_explicit_prefix():
    service = SchemaInferenceService(project_service=None)
    input_schema = [ColumnSchema(name="dt", type="date", source_node="s", is_calculated=False)]
    node = _node("date_extract", {
        "column": "dt",
        "parts": ["year", "month", "day"],
        "prefix": "ext_",
    })
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    names = [c.name for c in out]
    assert names == ["dt", "ext_year", "ext_month", "ext_day"]
    assert all(c.type == "int" for c in out[1:])


def test_date_extract_invalid_parts_ignored():
    """Parts non riconosciute sono ignorate (allineato al runtime)."""
    service = SchemaInferenceService(project_service=None)
    input_schema = [ColumnSchema(name="dt", type="date", source_node="s", is_calculated=False)]
    node = _node("date_extract", {
        "column": "dt",
        "parts": ["year", "millennium", "fortnight", "month"],
    })
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["dt", "dt_year", "dt_month"]


def test_date_extract_string_parts_normalized_to_list():
    """Tolleranza al config: `parts` come str singola → list."""
    service = SchemaInferenceService(project_service=None)
    input_schema = [ColumnSchema(name="dt", type="date", source_node="s", is_calculated=False)]
    node = _node("date_extract", {"column": "dt", "parts": "month"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["dt", "dt_month"]


def test_date_extract_no_column_passthrough():
    service, input_schema = _service_and_input()
    node = _node("date_extract", {})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b"]


# geo_distance --------------------------------------------------------------


def test_geo_distance_uses_left_input_only():
    """`geo_distance` e' multi-input. Lo schema output e' LEFT +
    `output_column`. Il right NON contribuisce colonne."""
    service = SchemaInferenceService(project_service=None)
    left = [
        ColumnSchema(name="id", type="int", source_node="L", is_calculated=False),
        ColumnSchema(name="geometry", type="geometry:point", source_node="L", is_calculated=False),
    ]
    right = [
        ColumnSchema(name="poi_id", type="int", source_node="R", is_calculated=False),
        ColumnSchema(name="geom_r", type="geometry:polygon", source_node="R", is_calculated=False),
    ]
    node = _node("geo_distance", {
        "left_geom_column": "geometry",
        "right_geom_column": "geom_r",
    })
    out = service._infer_transform_schema(node, [left, right], edges_by_target={})
    names = [c.name for c in out]
    assert names == ["id", "geometry", "distance_m"]
    assert out[-1].type == "real"


def test_geo_distance_custom_output_column():
    service = SchemaInferenceService(project_service=None)
    left = [ColumnSchema(name="x", type="int", source_node="L", is_calculated=False)]
    right = [ColumnSchema(name="y", type="int", source_node="R", is_calculated=False)]
    node = _node("geo_distance", {"output_column": "dist_km"})
    out = service._infer_transform_schema(node, [left, right], edges_by_target={})
    assert [c.name for c in out] == ["x", "dist_km"]


# geo_to_wkt ---------------------------------------------------------------


def test_geo_to_wkt_default_mode_adds_wkt_column():
    service = SchemaInferenceService(project_service=None)
    input_schema = [
        ColumnSchema(name="id", type="int", source_node="s", is_calculated=False),
        ColumnSchema(name="geometry", type="geometry:point", source_node="s", is_calculated=False),
    ]
    node = _node("geo_to_wkt", {"geom_column": "geometry"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["id", "geometry", "geometry_wkt"]
    assert out[-1].type == "str"


def test_geo_to_wkt_latlon_mode_adds_two_columns():
    service = SchemaInferenceService(project_service=None)
    input_schema = [ColumnSchema(name="id", type="int", source_node="s", is_calculated=False)]
    node = _node("geo_to_wkt", {"geom_column": "g", "mode": "latlon"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    names = [c.name for c in out]
    assert names == ["id", "latitude", "longitude"]
    assert all(c.type == "real" for c in out[1:])


def test_geo_to_wkt_both_mode_adds_three_columns():
    service = SchemaInferenceService(project_service=None)
    input_schema = [ColumnSchema(name="id", type="int", source_node="s", is_calculated=False)]
    node = _node("geo_to_wkt", {
        "geom_column": "g",
        "mode": "both",
        "wkt_column": "wkt_str",
        "lat_column": "lat",
        "lon_column": "lon",
    })
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["id", "wkt_str", "lat", "lon"]


def test_geo_to_wkt_unknown_mode_falls_back_to_wkt():
    service = SchemaInferenceService(project_service=None)
    input_schema = [ColumnSchema(name="id", type="int", source_node="s", is_calculated=False)]
    node = _node("geo_to_wkt", {"geom_column": "g", "mode": "weird_unknown"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["id", "geometry_wkt"]


# ---------------------------------------------------------------------------
# 6. Behavioral tests — BE-PHASE2-SCHEMA-INFER-003 batch 2
# ---------------------------------------------------------------------------


def _geo_input():
    return [
        ColumnSchema(name="id", type="int", source_node="s", is_calculated=False),
        ColumnSchema(name="geometry", type="geometry", source_node="s", is_calculated=False),
    ]


# geo_length ---------------------------------------------------------------


def test_geo_length_default_output_column():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_length", {"geom_column": "geometry"})
    out = service._infer_transform_schema(node, [_geo_input()], edges_by_target={})
    assert [c.name for c in out] == ["id", "geometry", "length_m"]
    assert out[-1].type == "real"
    assert out[-1].is_calculated is True


def test_geo_length_custom_output_column():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_length", {"geom_column": "geometry", "output_column": "lung"})
    out = service._infer_transform_schema(node, [_geo_input()], edges_by_target={})
    assert [c.name for c in out] == ["id", "geometry", "lung"]


def test_geo_length_collision_does_not_duplicate():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_length", {"output_column": "id"})
    out = service._infer_transform_schema(node, [_geo_input()], edges_by_target={})
    assert [c.name for c in out] == ["id", "geometry"]


# geo_perimeter ------------------------------------------------------------


def test_geo_perimeter_default_output_column():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_perimeter", {"geom_column": "geometry"})
    out = service._infer_transform_schema(node, [_geo_input()], edges_by_target={})
    assert [c.name for c in out] == ["id", "geometry", "perimeter_m"]
    assert out[-1].type == "real"


def test_geo_perimeter_custom_output_column():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_perimeter", {"output_column": "perim"})
    out = service._infer_transform_schema(node, [_geo_input()], edges_by_target={})
    assert [c.name for c in out] == ["id", "geometry", "perim"]


# geo_vertex_count ---------------------------------------------------------


def test_geo_vertex_count_default():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_vertex_count", {"geom_column": "geometry"})
    out = service._infer_transform_schema(node, [_geo_input()], edges_by_target={})
    assert [c.name for c in out] == ["id", "geometry", "vertex_count"]
    assert out[-1].type == "int"


def test_geo_vertex_count_custom_output_column():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_vertex_count", {"output_column": "n_pts"})
    out = service._infer_transform_schema(node, [_geo_input()], edges_by_target={})
    assert [c.name for c in out] == ["id", "geometry", "n_pts"]


# string_extract -----------------------------------------------------------


def test_string_extract_simple_pattern_default_output():
    service, input_schema = _service_and_input()
    node = _node("string_extract", {"column": "b", "pattern": r"(\d+)"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "b_extracted"]
    assert out[-1].type == "str"


def test_string_extract_simple_pattern_explicit_output():
    service, input_schema = _service_and_input()
    node = _node("string_extract", {
        "column": "b",
        "pattern": r"(\d+)",
        "output_column": "digits",
    })
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "digits"]


def test_string_extract_named_groups_yields_one_column_per_group():
    """Pattern con gruppi nominati → schema dichiara una colonna str
    per ciascun gruppo (in ordine di pattern). `output_column` ignorata
    quando ci sono named groups (allineato al runtime)."""
    service, input_schema = _service_and_input()
    node = _node("string_extract", {
        "column": "b",
        "pattern": r"(?P<via>.+),\s*(?P<civico>\d+)",
        "output_column": "ignored_when_named",
    })
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "via", "civico"]
    assert all(c.type == "str" for c in out[2:])


def test_string_extract_invalid_regex_passthrough():
    """Pattern regex invalido → runtime ritorna df invariato. Schema
    mirror: nessuna colonna aggiunta."""
    service, input_schema = _service_and_input()
    node = _node("string_extract", {"column": "b", "pattern": "(unclosed["})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b"]


def test_string_extract_missing_column_passthrough():
    service, input_schema = _service_and_input()
    node = _node("string_extract", {"column": "does_not_exist", "pattern": r"(\d+)"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b"]


def test_string_extract_missing_pattern_passthrough():
    service, input_schema = _service_and_input()
    node = _node("string_extract", {"column": "b"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b"]


# bin ----------------------------------------------------------------------


def test_bin_default_output_column():
    service, input_schema = _service_and_input()
    node = _node("bin", {"column": "a", "bins": 5})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "a_bin"]
    assert out[-1].type == "str"


def test_bin_explicit_output_column():
    service, input_schema = _service_and_input()
    node = _node("bin", {
        "column": "a",
        "bins": [0, 10, 50, 100],
        "labels": ["low", "mid", "high"],
        "output_column": "fascia",
    })
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "fascia"]


def test_bin_missing_column_passthrough():
    service, input_schema = _service_and_input()
    node = _node("bin", {"column": "missing"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b"]


# string_pad ---------------------------------------------------------------


def test_string_pad_overwrite_changes_column_type_to_str():
    """Senza `output_column`, la colonna sorgente viene sovrascritta:
    il runtime forza `astype(str)` quindi il tipo cambia in `str`."""
    service, input_schema = _service_and_input()
    node = _node("string_pad", {"column": "a", "width": 5})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b"]
    a_col = next(c for c in out if c.name == "a")
    assert a_col.type == "str"
    assert a_col.is_calculated is True
    # b non viene toccato
    b_col = next(c for c in out if c.name == "b")
    assert b_col.type == "str"
    assert b_col.is_calculated is False


def test_string_pad_explicit_output_column_adds_str_column():
    """Con `output_column != column`, la sorgente resta intatta e
    viene aggiunta una nuova colonna str."""
    service, input_schema = _service_and_input()
    node = _node("string_pad", {
        "column": "a",
        "output_column": "a_padded",
        "width": 4,
        "fill_char": "0",
    })
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "a_padded"]
    a_col = next(c for c in out if c.name == "a")
    assert a_col.type == "int"  # tipo originale preservato
    assert out[-1].type == "str"
    assert out[-1].is_calculated is True


def test_string_pad_explicit_output_collision_overwrites_to_str():
    """Se `output_column` collide con un'altra colonna esistente, la
    collisione viene sovrascritta a `str`."""
    service, input_schema = _service_and_input()
    node = _node("string_pad", {
        "column": "a",
        "output_column": "b",
        "width": 4,
    })
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    names = [c.name for c in out]
    assert names == ["a", "b"]
    b_col = next(c for c in out if c.name == "b")
    assert b_col.type == "str"
    assert b_col.is_calculated is True
    # a (sorgente) preservata
    a_col = next(c for c in out if c.name == "a")
    assert a_col.type == "int"


def test_string_pad_missing_column_passthrough():
    service, input_schema = _service_and_input()
    node = _node("string_pad", {"column": "missing", "width": 5})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [(c.name, c.type) for c in out] == [("a", "int"), ("b", "str")]


def test_string_pad_no_column_passthrough():
    service, input_schema = _service_and_input()
    node = _node("string_pad", {"width": 5})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [(c.name, c.type) for c in out] == [("a", "int"), ("b", "str")]


# ---------------------------------------------------------------------------
# 7. Behavioral tests — BE-PHASE2-SCHEMA-INFER-004 batch 3
# ---------------------------------------------------------------------------


def _poly_input():
    return [
        ColumnSchema(name="poly_id", type="int", source_node="L", is_calculated=False),
        ColumnSchema(name="name", type="str", source_node="L", is_calculated=False),
        ColumnSchema(name="geometry", type="geometry:polygon", source_node="L", is_calculated=False),
    ]


def _point_input():
    return [
        ColumnSchema(name="point_id", type="int", source_node="R", is_calculated=False),
        ColumnSchema(name="value", type="real", source_node="R", is_calculated=False),
        ColumnSchema(name="geometry", type="geometry:point", source_node="R", is_calculated=False),
    ]


# geo_bounds_extractor -----------------------------------------------------


def test_geo_bounds_extractor_default_no_prefix():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_bounds_extractor", {"geom_column": "geometry"})
    out = service._infer_transform_schema(node, [_poly_input()], edges_by_target={})
    assert [c.name for c in out] == [
        "poly_id", "name", "geometry",
        "min_x", "min_y", "max_x", "max_y",
    ]
    assert all(c.type == "real" for c in out[-4:])
    assert all(c.is_calculated is True for c in out[-4:])


def test_geo_bounds_extractor_with_prefix():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_bounds_extractor", {"geom_column": "geometry", "prefix": "bbox_"})
    out = service._infer_transform_schema(node, [_poly_input()], edges_by_target={})
    bbox_cols = [c.name for c in out if c.name.startswith("bbox_")]
    assert bbox_cols == ["bbox_min_x", "bbox_min_y", "bbox_max_x", "bbox_max_y"]


def test_geo_bounds_extractor_collision_skips_existing():
    """Se un nome bbox collide con una colonna esistente, non viene
    duplicato nello schema dichiarato."""
    service = SchemaInferenceService(project_service=None)
    base = _poly_input() + [
        ColumnSchema(name="min_x", type="real", source_node="L", is_calculated=False),
    ]
    node = _node("geo_bounds_extractor", {"geom_column": "geometry"})
    out = service._infer_transform_schema(node, [base], edges_by_target={})
    names = [c.name for c in out]
    assert names.count("min_x") == 1
    # le altre 3 sono comunque aggiunte
    for col in ("min_y", "max_x", "max_y"):
        assert col in names


# geo_count_points_in_polygons --------------------------------------------


def test_geo_count_points_in_polygons_default_only_count():
    """Senza `aggregate_column`: solo `point_count` (int) aggiunto al
    LEFT (poligoni). RIGHT non contribuisce colonne."""
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_count_points_in_polygons", {
        "polygon_geom_column": "geometry",
        "point_geom_column": "geometry",
    })
    out = service._infer_transform_schema(
        node, [_poly_input(), _point_input()], edges_by_target={},
    )
    assert [c.name for c in out] == [
        "poly_id", "name", "geometry", "point_count",
    ]
    assert out[-1].type == "int"
    assert out[-1].is_calculated is True


def test_geo_count_points_in_polygons_custom_count_column():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_count_points_in_polygons", {
        "polygon_geom_column": "geometry",
        "point_geom_column": "geometry",
        "count_column": "n_points",
    })
    out = service._infer_transform_schema(
        node, [_poly_input(), _point_input()], edges_by_target={},
    )
    assert [c.name for c in out][-1:] == ["n_points"]


def test_geo_count_points_in_polygons_with_aggregate():
    """Con `aggregate_column` valorizzata: aggiunge anche
    `f"{aggregate_column}_{aggregate_func}"` (real)."""
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_count_points_in_polygons", {
        "polygon_geom_column": "geometry",
        "point_geom_column": "geometry",
        "aggregate_column": "value",
        "aggregate_func": "mean",
    })
    out = service._infer_transform_schema(
        node, [_poly_input(), _point_input()], edges_by_target={},
    )
    assert [c.name for c in out] == [
        "poly_id", "name", "geometry", "point_count", "value_mean",
    ]
    assert out[-1].type == "real"


def test_geo_count_points_in_polygons_unknown_func_falls_back_to_sum():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_count_points_in_polygons", {
        "polygon_geom_column": "geometry",
        "point_geom_column": "geometry",
        "aggregate_column": "value",
        "aggregate_func": "weird_unknown",
    })
    out = service._infer_transform_schema(
        node, [_poly_input(), _point_input()], edges_by_target={},
    )
    assert [c.name for c in out][-1] == "value_sum"


# geo_line_builder ---------------------------------------------------------


def _line_builder_input():
    return [
        ColumnSchema(name="track_id", type="int", source_node="s", is_calculated=False),
        ColumnSchema(name="ts", type="date", source_node="s", is_calculated=False),
        ColumnSchema(name="extra", type="str", source_node="s", is_calculated=False),
        ColumnSchema(name="geometry", type="geometry:point", source_node="s", is_calculated=False),
    ]


def test_geo_line_builder_rebuilds_to_two_columns():
    """Il runtime restituisce SOLO `[group_column, output_column]`.
    Tutte le altre colonne sono scartate."""
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_line_builder", {
        "geom_column": "geometry",
        "group_column": "track_id",
        "order_column": "ts",
    })
    out = service._infer_transform_schema(
        node, [_line_builder_input()], edges_by_target={},
    )
    assert [c.name for c in out] == ["track_id", "geometry"]
    assert out[0].type == "int"  # group_column type preservato
    assert out[1].type == "geometry:line"
    assert out[1].is_calculated is True


def test_geo_line_builder_custom_output_column():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_line_builder", {
        "geom_column": "geometry",
        "group_column": "track_id",
        "output_column": "track_line",
    })
    out = service._infer_transform_schema(
        node, [_line_builder_input()], edges_by_target={},
    )
    assert [c.name for c in out] == ["track_id", "track_line"]
    assert out[1].type == "geometry:line"


def test_geo_line_builder_missing_group_column_passthrough():
    """Senza `group_column` il runtime crash: schema-inference non
    indovina, ritorna l'input invariato."""
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_line_builder", {"geom_column": "geometry"})
    out = service._infer_transform_schema(
        node, [_line_builder_input()], edges_by_target={},
    )
    assert [c.name for c in out] == ["track_id", "ts", "extra", "geometry"]


# geo_polygon_builder ------------------------------------------------------


def test_geo_polygon_builder_rebuilds_to_two_columns():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_polygon_builder", {
        "geom_column": "geometry",
        "group_column": "track_id",
    })
    out = service._infer_transform_schema(
        node, [_line_builder_input()], edges_by_target={},
    )
    assert [c.name for c in out] == ["track_id", "geometry"]
    assert out[1].type == "geometry:polygon"


def test_geo_polygon_builder_unknown_group_column_uses_str_fallback():
    """Se `group_column` e' specificata ma assente dall'input, il
    tipo di fallback per la prima colonna e' `str` (best-effort)."""
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_polygon_builder", {
        "geom_column": "geometry",
        "group_column": "missing_group",
    })
    out = service._infer_transform_schema(
        node, [_line_builder_input()], edges_by_target={},
    )
    assert [c.name for c in out] == ["missing_group", "geometry"]
    assert out[0].type == "str"


# geo_point_on_surface ----------------------------------------------------


def test_geo_point_on_surface_default_adds_new_column():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_point_on_surface", {"geom_column": "geometry"})
    out = service._infer_transform_schema(node, [_poly_input()], edges_by_target={})
    assert [c.name for c in out] == [
        "poly_id", "name", "geometry", "point_on_surface",
    ]
    assert out[-1].type == "geometry:point"


def test_geo_point_on_surface_overwrites_when_same_as_geom_column():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_point_on_surface", {
        "geom_column": "geometry",
        "output_column": "geometry",
    })
    out = service._infer_transform_schema(node, [_poly_input()], edges_by_target={})
    assert [c.name for c in out] == ["poly_id", "name", "geometry"]
    geom = next(c for c in out if c.name == "geometry")
    assert geom.type == "geometry:point"
    assert geom.is_calculated is True


def test_geo_point_on_surface_custom_output_column():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_point_on_surface", {
        "geom_column": "geometry",
        "output_column": "label_pt",
    })
    out = service._infer_transform_schema(node, [_poly_input()], edges_by_target={})
    assert [c.name for c in out] == [
        "poly_id", "name", "geometry", "label_pt",
    ]
    assert out[-1].type == "geometry:point"


# geo_boundary ------------------------------------------------------------


def test_geo_boundary_default_adds_generic_geometry_column():
    """Sottotipo dipende dall'input (poly→line, line→multipoint),
    quindi tipo dichiarato e' generico `"geometry"`."""
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_boundary", {"geom_column": "geometry"})
    out = service._infer_transform_schema(node, [_poly_input()], edges_by_target={})
    assert [c.name for c in out] == [
        "poly_id", "name", "geometry", "boundary",
    ]
    assert out[-1].type == "geometry"


def test_geo_boundary_overwrite_demotes_subtype():
    """`output_column == geom_column`: la geometria sorgente perde il
    sottotipo originale (poly) e diventa `"geometry"` generico."""
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_boundary", {
        "geom_column": "geometry",
        "output_column": "geometry",
    })
    out = service._infer_transform_schema(node, [_poly_input()], edges_by_target={})
    geom = next(c for c in out if c.name == "geometry")
    assert geom.type == "geometry"
    assert geom.is_calculated is True


def test_geo_boundary_custom_output_column():
    service = SchemaInferenceService(project_service=None)
    node = _node("geo_boundary", {
        "geom_column": "geometry",
        "output_column": "outline",
    })
    out = service._infer_transform_schema(node, [_poly_input()], edges_by_target={})
    assert [c.name for c in out] == [
        "poly_id", "name", "geometry", "outline",
    ]
    assert out[-1].type == "geometry"


# ---------------------------------------------------------------------------
# 8. Behavioral tests — BE-PHASE2-SCHEMA-INFER-005 batch 4
# ---------------------------------------------------------------------------
#
# 4 transform riclassificati come PASSTHROUGH_OK_GAP via verifica
# runtime (nessun handler dedicato richiesto, restano in fallback):
#   - dedup_advanced
#   - geo_clean_topology
#   - geo_make_valid
#   - geo_explode
#
# 2 transform promossi a dispatcher branch dedicato:
#   - reorder_columns
#   - geo_voronoi


def _input3() -> list:
    return [
        ColumnSchema(name="a", type="int", source_node="s", is_calculated=False),
        ColumnSchema(name="b", type="str", source_node="s", is_calculated=False),
        ColumnSchema(name="c", type="real", source_node="s", is_calculated=False),
    ]


# reorder_columns ----------------------------------------------------------


def test_reorder_columns_explicit_order():
    service = SchemaInferenceService(project_service=None)
    node = _node("reorder_columns", {"columns": ["c", "a"]})
    out = service._infer_transform_schema(node, [_input3()], edges_by_target={})
    # `c` e `a` davanti, `b` (non specificata) in coda.
    assert [c.name for c in out] == ["c", "a", "b"]
    # Tipi preservati.
    types = {c.name: c.type for c in out}
    assert types == {"a": "int", "b": "str", "c": "real"}


def test_reorder_columns_ignores_unknown_names():
    service = SchemaInferenceService(project_service=None)
    node = _node("reorder_columns", {"columns": ["zzz", "c", "missing", "a"]})
    out = service._infer_transform_schema(node, [_input3()], edges_by_target={})
    # I nomi inesistenti vengono filtrati (allineato al runtime).
    assert [c.name for c in out] == ["c", "a", "b"]


def test_reorder_columns_alphabetical():
    service = SchemaInferenceService(project_service=None)
    node = _node("reorder_columns", {"sort_alphabetical": True})
    out = service._infer_transform_schema(node, [_input3()], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "c"]


def test_reorder_columns_alphabetical_ignored_when_columns_given():
    """`columns` ha priorita' su `sort_alphabetical` (allineato al
    runtime: l'ordinamento alfabetico scatta solo nel ramo `elif`)."""
    service = SchemaInferenceService(project_service=None)
    node = _node("reorder_columns", {
        "columns": ["c", "a"],
        "sort_alphabetical": True,
    })
    out = service._infer_transform_schema(node, [_input3()], edges_by_target={})
    assert [c.name for c in out] == ["c", "a", "b"]


def test_reorder_columns_default_no_change():
    """Senza `columns` ne' `sort_alphabetical=True` lo schema resta
    invariato."""
    service = SchemaInferenceService(project_service=None)
    node = _node("reorder_columns", {})
    out = service._infer_transform_schema(node, [_input3()], edges_by_target={})
    assert [c.name for c in out] == ["a", "b", "c"]


# geo_voronoi --------------------------------------------------------------


def test_geo_voronoi_default_replaces_geometry_type_to_polygon():
    """Voronoi sostituisce il tipo della colonna geometria a
    `geometry:polygon`. Tutte le altre colonne restano invariate."""
    service = SchemaInferenceService(project_service=None)
    input_schema = [
        ColumnSchema(name="poi_id", type="int", source_node="s", is_calculated=False),
        ColumnSchema(name="name", type="str", source_node="s", is_calculated=False),
        ColumnSchema(name="geometry", type="geometry:point", source_node="s", is_calculated=False),
    ]
    node = _node("geo_voronoi", {"geom_column": "geometry"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [c.name for c in out] == ["poi_id", "name", "geometry"]
    geom = next(c for c in out if c.name == "geometry")
    assert geom.type == "geometry:polygon"
    assert geom.is_calculated is True
    # le altre colonne restano non calcolate.
    poi_id = next(c for c in out if c.name == "poi_id")
    assert poi_id.is_calculated is False


def test_geo_voronoi_default_geom_column_when_unset():
    """Se `geom_column` non e' nella config, default a `"geometry"`."""
    service = SchemaInferenceService(project_service=None)
    input_schema = [
        ColumnSchema(name="geometry", type="geometry:point", source_node="s", is_calculated=False),
    ]
    node = _node("geo_voronoi", {})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert out[0].type == "geometry:polygon"


def test_geo_voronoi_geom_column_missing_passthrough():
    """Se `geom_column` non esiste nello schema input, passthrough
    invariato (caso degenere)."""
    service = SchemaInferenceService(project_service=None)
    input_schema = [
        ColumnSchema(name="x", type="real", source_node="s", is_calculated=False),
    ]
    node = _node("geo_voronoi", {"geom_column": "missing_geom"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [(c.name, c.type) for c in out] == [("x", "real")]


# Rows-only reclassification — passthrough behavioral checks ---------------


@pytest.mark.parametrize("op", [
    "dedup_advanced",
    "geo_clean_topology",
    "geo_make_valid",
    "geo_explode",
])
def test_rows_only_ops_preserve_schema_via_passthrough(op):
    """I 4 transform rows-only sono in PASSTHROUGH_OK_GAP: cadono nel
    fallback `_schema_passthrough` con warning e producono lo stesso
    column-set dell'input. Verifica end-to-end."""
    service, input_schema = _service_and_input()
    node = _node(op, {"geom_column": "geometry"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [(c.name, c.type) for c in out] == [
        (c.name, c.type) for c in input_schema
    ]


def test_rows_only_passthrough_ok_membership_is_locked():
    """Lock esplicito: i 4 transform rows-only DEVONO essere in
    PASSTHROUGH_OK_GAP (non in PASSTHROUGH_WRONG_GAP) dopo
    BE-PHASE2-SCHEMA-INFER-005. Drift in meno = riclassificazione
    persa nel refactor; drift in piu' su PASSTHROUGH_WRONG_GAP =
    qualcuno li ha rimessi nel set sbagliato."""
    expected_in_ok = {
        "dedup_advanced", "geo_clean_topology",
        "geo_make_valid", "geo_explode",
    }
    assert expected_in_ok.issubset(PASSTHROUGH_OK_GAP)
    assert expected_in_ok.isdisjoint(PASSTHROUGH_WRONG_GAP)


# ---------------------------------------------------------------------------
# 9. Behavioral tests — BE-PHASE2-SCHEMA-INFER-006 batch 5
# ---------------------------------------------------------------------------
#
# 6 transform multi-input/lookup promossi a dispatcher dedicato:
#   - cross_join, geo_within, geo_overlay, geo_symmetric_difference,
#     geo_nearest, lookup.
# `table_diff` saltato in questo batch (output schema deterministico
# ma elaborato — deferito al prossimo batch).


def _left_attr_input() -> list:
    return [
        ColumnSchema(name="id", type="int", source_node="L", is_calculated=False),
        ColumnSchema(name="name", type="str", source_node="L", is_calculated=False),
        ColumnSchema(name="geometry", type="geometry:point", source_node="L", is_calculated=False),
    ]


def _right_attr_input() -> list:
    return [
        ColumnSchema(name="id", type="int", source_node="R", is_calculated=False),
        ColumnSchema(name="category", type="str", source_node="R", is_calculated=False),
        ColumnSchema(name="geometry", type="geometry:polygon", source_node="R", is_calculated=False),
    ]


# cross_join ---------------------------------------------------------------


def test_cross_join_no_collisions_preserves_names():
    service = SchemaInferenceService(project_service=None)
    left = [
        ColumnSchema(name="a", type="int", source_node="L", is_calculated=False),
    ]
    right = [
        ColumnSchema(name="b", type="str", source_node="R", is_calculated=False),
    ]
    node = _node("cross_join", {})
    out = service._infer_transform_schema(node, [left, right], edges_by_target={})
    assert [c.name for c in out] == ["a", "b"]


def test_cross_join_collisions_apply_x_y_suffixes():
    """Pandas pd.merge how='cross' applica suffissi default
    `('_x', '_y')` ad ENTRAMBE le colonne in collisione."""
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("cross_join", {}),
        [_left_attr_input(), _right_attr_input()],
        edges_by_target={},
    )
    names = [c.name for c in out]
    # `id` e `geometry` sono in collisione → entrambe suffissate
    # `name` (LEFT-only) e `category` (RIGHT-only) restano.
    assert names == [
        "id_x", "name", "geometry_x",
        "id_y", "category", "geometry_y",
    ]


def test_cross_join_with_single_input_passthrough():
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("cross_join", {}),
        [_left_attr_input()],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["id", "name", "geometry"]


# geo_within ---------------------------------------------------------------


def test_geo_within_outputs_left_only():
    """`geo_within` proietta solo le colonne LEFT. RIGHT non
    contribuisce."""
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("geo_within", {
            "left_geom_column": "geometry",
            "right_geom_column": "geometry",
        }),
        [_left_attr_input(), _right_attr_input()],
        edges_by_target={},
    )
    assert [(c.name, c.type) for c in out] == [
        ("id", "int"), ("name", "str"), ("geometry", "geometry:point"),
    ]


# geo_overlay --------------------------------------------------------------


def test_geo_overlay_dispatch_uses_existing_overlay_helper():
    """`geo_overlay` riusa `_schema_geo_overlay`: LEFT cols + RIGHT
    non-geom (con suffisso `_2` su collisione). La geometria del
    RIGHT NON e' inclusa."""
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("geo_overlay", {
            "left_geom_column": "geometry",
            "right_geom_column": "geometry",
            "how": "intersection",
        }),
        [_left_attr_input(), _right_attr_input()],
        edges_by_target={},
    )
    names = [c.name for c in out]
    # LEFT preservato; RIGHT.id collide → `id_2`, RIGHT.category aggiunta
    # senza collisione, RIGHT.geometry skip (geometry-typed).
    assert names == ["id", "name", "geometry", "id_2", "category"]


def test_geo_overlay_default_how_is_intersection():
    service = SchemaInferenceService(project_service=None)
    # Senza `how` esplicito, il dispatcher usa "intersection" come default.
    out = service._infer_transform_schema(
        _node("geo_overlay", {}),
        [_left_attr_input(), _right_attr_input()],
        edges_by_target={},
    )
    names = [c.name for c in out]
    assert names == ["id", "name", "geometry", "id_2", "category"]


# geo_symmetric_difference -------------------------------------------------


def test_geo_symmetric_difference_uses_overlay_helper():
    """Riuso dello stesso helper `_schema_geo_overlay` con
    `how="symmetric_difference"` hardcoded."""
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("geo_symmetric_difference", {
            "left_geom_column": "geometry",
            "right_geom_column": "geometry",
        }),
        [_left_attr_input(), _right_attr_input()],
        edges_by_target={},
    )
    assert [c.name for c in out] == [
        "id", "name", "geometry", "id_2", "category",
    ]


# geo_nearest --------------------------------------------------------------


def test_geo_nearest_combines_left_with_right_non_geom_plus_distance():
    """`geo_nearest`: LEFT + RIGHT (no geom, suffisso `_right` su
    collisione) + distance column. NON deve contenere `index_right`
    (droppata dal runtime)."""
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("geo_nearest", {
            "left_geom_column": "geometry",
            "right_geom_column": "geometry",
        }),
        [_left_attr_input(), _right_attr_input()],
        edges_by_target={},
    )
    names = [c.name for c in out]
    # LEFT preservato; RIGHT.id collide → `id_right`, RIGHT.category
    # senza collisione, RIGHT.geometry skip.
    assert names == ["id", "name", "geometry", "id_right", "category", "distance_m"]
    assert "index_right" not in names
    assert out[-1].type == "real"
    assert out[-1].is_calculated is True


def test_geo_nearest_custom_distance_column():
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("geo_nearest", {
            "left_geom_column": "geometry",
            "right_geom_column": "geometry",
            "output_column": "dist_km",
        }),
        [_left_attr_input(), _right_attr_input()],
        edges_by_target={},
    )
    assert [c.name for c in out][-1] == "dist_km"


def test_geo_nearest_single_input_passthrough():
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("geo_nearest", {}),
        [_left_attr_input()],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["id", "name", "geometry"]


# lookup -------------------------------------------------------------------


def test_lookup_overwrite_preserves_schema():
    """Senza `output_column`: in-place overwrite. Schema-set invariato
    (tipo originale preservato come approssimazione)."""
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("lookup", {"column": "b", "mapping": {"x": "y"}}),
        [input_schema],
        edges_by_target={},
    )
    assert [(c.name, c.type) for c in out] == [("a", "int"), ("b", "str")]


def test_lookup_explicit_output_column_adds_column_with_source_type():
    """Con `output_column != column`: aggiunge una nuova colonna con
    tipo = tipo della sorgente (approssimazione documentata)."""
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("lookup", {
            "column": "b",
            "mapping": {"x": "y"},
            "output_column": "b_mapped",
        }),
        [input_schema],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["a", "b", "b_mapped"]
    new_col = next(c for c in out if c.name == "b_mapped")
    assert new_col.type == "str"
    assert new_col.is_calculated is True


def test_lookup_collision_with_existing_skips_add():
    """Se `output_column` collide con una colonna esistente diversa
    da `column`, schema invariato (skip add)."""
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("lookup", {
            "column": "b",
            "mapping": {"x": "y"},
            "output_column": "a",
        }),
        [input_schema],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["a", "b"]


def test_lookup_missing_column_passthrough():
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("lookup", {"column": "missing", "mapping": {"x": "y"}}),
        [input_schema],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["a", "b"]


def test_lookup_empty_mapping_passthrough():
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("lookup", {"column": "b", "mapping": {}}),
        [input_schema],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["a", "b"]


# ---------------------------------------------------------------------------
# 10. Behavioral tests — BE-PHASE2-SCHEMA-INFER-007 batch 6
# ---------------------------------------------------------------------------
#
# 4 transform deterministici promossi a dispatcher dedicato:
#   - table_diff, melt, statistics, window_function
# 2 transform `MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT` (output
# data-dependent, non inferibili staticamente):
#   - flatten_json, transpose


# table_diff ---------------------------------------------------------------


def _diff_left() -> list:
    return [
        ColumnSchema(name="id", type="int", source_node="L", is_calculated=False),
        ColumnSchema(name="name", type="str", source_node="L", is_calculated=False),
        ColumnSchema(name="price", type="real", source_node="L", is_calculated=False),
        ColumnSchema(name="legacy_only", type="str", source_node="L", is_calculated=False),
    ]


def _diff_right(right_key_name: str = "id") -> list:
    return [
        ColumnSchema(name=right_key_name, type="int", source_node="R", is_calculated=False),
        ColumnSchema(name="name", type="str", source_node="R", is_calculated=False),
        ColumnSchema(name="price", type="real", source_node="R", is_calculated=False),
        ColumnSchema(name="new_only", type="bool", source_node="R", is_calculated=False),
    ]


def test_table_diff_full_schema_with_matching_keys():
    """key + common (LEFT types) + left_only + right_only + 3 fixed
    diff columns."""
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("table_diff", {
            "left_keys": ["id"],
            "right_keys": ["id"],
        }),
        [_diff_left(), _diff_right()],
        edges_by_target={},
    )
    names = [c.name for c in out]
    assert names == [
        "id",            # key
        "name", "price", # common (LEFT types)
        "legacy_only",   # only_old
        "new_only",      # only_new (RIGHT type)
        "_diff_status", "_diff_columns", "_diff_old_values",
    ]
    types = {c.name: c.type for c in out}
    assert types["id"] == "int"
    assert types["price"] == "real"
    assert types["new_only"] == "bool"
    assert types["_diff_status"] == "str"
    assert types["_diff_columns"] == "str"
    assert types["_diff_old_values"] == "str"


def test_table_diff_compare_columns_filters_common_only():
    """`compare_columns` filtra solo `common_value_cols`. Le
    only_old/only_new restano intatte."""
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("table_diff", {
            "left_keys": ["id"],
            "right_keys": ["id"],
            "compare_columns": ["price"],
        }),
        [_diff_left(), _diff_right()],
        edges_by_target={},
    )
    names = [c.name for c in out]
    # `name` (common ma non in compare_columns) viene escluso;
    # legacy_only e new_only restano.
    assert names == [
        "id", "price", "legacy_only", "new_only",
        "_diff_status", "_diff_columns", "_diff_old_values",
    ]


def test_table_diff_renames_right_keys_to_left_names():
    """Quando `right_keys != left_keys`, il runtime rinomina la
    chiave RIGHT al nome LEFT. La chiave finale appare solo una
    volta, col nome LEFT."""
    service = SchemaInferenceService(project_service=None)
    right = _diff_right(right_key_name="record_id")
    out = service._infer_transform_schema(
        _node("table_diff", {
            "left_keys": ["id"],
            "right_keys": ["record_id"],
        }),
        [_diff_left(), right],
        edges_by_target={},
    )
    names = [c.name for c in out]
    assert "record_id" not in names
    assert names[0] == "id"


def test_table_diff_missing_keys_passthrough():
    """`left_keys`/`right_keys` mancanti → passthrough di LEFT."""
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("table_diff", {}),
        [_diff_left(), _diff_right()],
        edges_by_target={},
    )
    assert [c.name for c in out] == [
        "id", "name", "price", "legacy_only",
    ]


def test_table_diff_keys_length_mismatch_passthrough():
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("table_diff", {
            "left_keys": ["id", "name"],
            "right_keys": ["id"],
        }),
        [_diff_left(), _diff_right()],
        edges_by_target={},
    )
    assert [c.name for c in out] == [
        "id", "name", "price", "legacy_only",
    ]


def test_table_diff_accepts_string_keys_csv():
    """Il FE puo' inviare `left_keys`/`right_keys` come stringa
    comma-separated. Devono essere normalizzate a lista."""
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("table_diff", {
            "left_keys": "id",
            "right_keys": "id",
        }),
        [_diff_left(), _diff_right()],
        edges_by_target={},
    )
    names = [c.name for c in out]
    assert "_diff_status" in names


# melt ---------------------------------------------------------------------


def _wide_input() -> list:
    return [
        ColumnSchema(name="region", type="str", source_node="s", is_calculated=False),
        ColumnSchema(name="q1", type="real", source_node="s", is_calculated=False),
        ColumnSchema(name="q2", type="real", source_node="s", is_calculated=False),
        ColumnSchema(name="q3", type="real", source_node="s", is_calculated=False),
    ]


def test_melt_homogeneous_value_types_preserved():
    """Quando tutti i value_columns hanno lo stesso tipo, value_name
    eredita quel tipo."""
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("melt", {
            "id_columns": ["region"],
            "value_columns": ["q1", "q2", "q3"],
        }),
        [_wide_input()],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["region", "variable", "value"]
    types = {c.name: c.type for c in out}
    assert types["region"] == "str"
    assert types["variable"] == "str"
    assert types["value"] == "real"  # tutti i quarter sono real


def test_melt_heterogeneous_value_types_falls_back_to_str():
    """Tipi misti tra value_columns → value_name e' `str` (mirror
    della promozione pandas a object dtype)."""
    service = SchemaInferenceService(project_service=None)
    mixed = [
        ColumnSchema(name="id", type="int", source_node="s", is_calculated=False),
        ColumnSchema(name="a", type="int", source_node="s", is_calculated=False),
        ColumnSchema(name="b", type="str", source_node="s", is_calculated=False),
    ]
    out = service._infer_transform_schema(
        _node("melt", {
            "id_columns": ["id"],
            "value_columns": ["a", "b"],
        }),
        [mixed],
        edges_by_target={},
    )
    types = {c.name: c.type for c in out}
    assert types["value"] == "str"


def test_melt_default_value_columns_uses_all_non_id():
    """Senza `value_columns`, tutti i non-id_columns diventano
    value_columns."""
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("melt", {"id_columns": ["region"]}),
        [_wide_input()],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["region", "variable", "value"]
    # q1/q2/q3 sono tutti real → value e' real
    assert next(c for c in out if c.name == "value").type == "real"


def test_melt_custom_var_value_names():
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("melt", {
            "id_columns": ["region"],
            "var_name": "quarter",
            "value_name": "amount",
        }),
        [_wide_input()],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["region", "quarter", "amount"]


def test_melt_string_id_columns_csv():
    service = SchemaInferenceService(project_service=None)
    out = service._infer_transform_schema(
        _node("melt", {"id_columns": "region"}),
        [_wide_input()],
        edges_by_target={},
    )
    assert [c.name for c in out][0] == "region"


# statistics ---------------------------------------------------------------


def test_statistics_default_stats_six_columns():
    """Default stats: count (int) + min/max/mean/median/std (real),
    prefix derivato da `column`."""
    service, input_schema = _service_and_input()
    # `a` e' int — e' la colonna numerica typical
    out = service._infer_transform_schema(
        _node("statistics", {"column": "a"}),
        [input_schema],
        edges_by_target={},
    )
    names = [c.name for c in out]
    assert names == [
        "a", "b",
        "a_count", "a_min", "a_max", "a_mean", "a_median", "a_std",
    ]
    types = {c.name: c.type for c in out}
    assert types["a_count"] == "int"
    for stat in ("a_min", "a_max", "a_mean", "a_median", "a_std"):
        assert types[stat] == "real"


def test_statistics_custom_stats_subset():
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("statistics", {
            "column": "a",
            "stats": ["sum", "var", "q25", "q75"],
        }),
        [input_schema],
        edges_by_target={},
    )
    new_cols = [c for c in out if c.name not in ("a", "b")]
    assert [c.name for c in new_cols] == [
        "a_sum", "a_var", "a_q25", "a_q75",
    ]
    assert all(c.type == "real" for c in new_cols)


def test_statistics_unknown_stats_ignored():
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("statistics", {
            "column": "a",
            "stats": ["count", "weird_unknown", "min"],
        }),
        [input_schema],
        edges_by_target={},
    )
    names = [c.name for c in out]
    assert names == ["a", "b", "a_count", "a_min"]


def test_statistics_custom_prefix():
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("statistics", {
            "column": "a",
            "stats": ["count"],
            "output_prefix": "stat_",
        }),
        [input_schema],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["a", "b", "stat_count"]


def test_statistics_missing_column_passthrough():
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("statistics", {"column": "missing"}),
        [input_schema],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["a", "b"]


# window_function ----------------------------------------------------------


def test_window_function_rank_default_real_column():
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("window_function", {"column": "a", "function": "rank"}),
        [input_schema],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["a", "b", "a_rank"]
    assert out[-1].type == "real"


def test_window_function_cumcount_returns_int():
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("window_function", {"column": "a", "function": "cumcount"}),
        [input_schema],
        edges_by_target={},
    )
    assert out[-1].name == "a_cumcount"
    assert out[-1].type == "int"


def test_window_function_cumsum_preserves_source_type():
    """cumsum preserva il tipo della sorgente (int → int)."""
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("window_function", {"column": "a", "function": "cumsum"}),
        [input_schema],
        edges_by_target={},
    )
    assert out[-1].name == "a_cumsum"
    assert out[-1].type == "int"  # `a` e' int nell'input


def test_window_function_lag_preserves_source_type_for_str():
    service = SchemaInferenceService(project_service=None)
    input_schema = [
        ColumnSchema(name="city", type="str", source_node="s", is_calculated=False),
    ]
    out = service._infer_transform_schema(
        _node("window_function", {"column": "city", "function": "lag"}),
        [input_schema],
        edges_by_target={},
    )
    assert out[-1].type == "str"


def test_window_function_pct_change_returns_real():
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("window_function", {"column": "a", "function": "pct_change"}),
        [input_schema],
        edges_by_target={},
    )
    assert out[-1].type == "real"


def test_window_function_explicit_output_column():
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("window_function", {
            "column": "a",
            "function": "rank",
            "output_column": "ranking",
        }),
        [input_schema],
        edges_by_target={},
    )
    assert out[-1].name == "ranking"
    assert out[-1].type == "real"


def test_window_function_unknown_function_passthrough():
    """Funzione invalida → no-op (mirror runtime warning + return df)."""
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("window_function", {
            "column": "a",
            "function": "weird_unknown",
        }),
        [input_schema],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["a", "b"]


def test_window_function_missing_column_passthrough():
    service, input_schema = _service_and_input()
    out = service._infer_transform_schema(
        _node("window_function", {
            "column": "missing",
            "function": "rank",
        }),
        [input_schema],
        edges_by_target={},
    )
    assert [c.name for c in out] == ["a", "b"]


# ---------------------------------------------------------------------------
# 11. Classification lock — MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT
# ---------------------------------------------------------------------------


def test_migrate_next_set_matches_residual_passthrough_wrong():
    """Lock: tutto cio' che resta in PASSTHROUGH_WRONG_GAP DEVE
    essere classificato come MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT
    dopo BE-PHASE2-SCHEMA-INFER-007. Drift → un nuovo deterministico
    e' stato dimenticato fuori dal dispatcher, oppure il refactor ha
    riaperto un gap chiuso."""
    assert MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT == PASSTHROUGH_WRONG_GAP, (
        f"PASSTHROUGH_WRONG_GAP residuo non e' interamente "
        f"MIGRATE_NEXT. Diff: "
        f"in WRONG ma non in MIGRATE_NEXT: "
        f"{PASSTHROUGH_WRONG_GAP - MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT}; "
        f"in MIGRATE_NEXT ma non in WRONG: "
        f"{MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT - PASSTHROUGH_WRONG_GAP}."
    )


@pytest.mark.parametrize("op", sorted(MIGRATE_NEXT_NEEDS_RUNTIME_CONTRACT))
def test_migrate_next_ops_fall_through_to_passthrough(op):
    """I 2 transform `MIGRATE_NEXT` cadono nel fallback
    `_schema_passthrough` (warning logger). Lo schema dichiarato e'
    quello di LEFT — un'approssimazione esplicita, non una verita'.
    Documentato nei commenti del set + nel report di batch."""
    service, input_schema = _service_and_input()
    node = _node(op, {"column": "b"})
    out = service._infer_transform_schema(node, [input_schema], edges_by_target={})
    assert [(c.name, c.type) for c in out] == [
        (c.name, c.type) for c in input_schema
    ]
