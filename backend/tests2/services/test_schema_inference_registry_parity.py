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
    "cross_join",           # combina colonne L+R
    "flatten_json",         # esplode chiavi JSON in colonne
    "geo_nearest",          # aggiunge colonne neighbour
    "geo_overlay",          # aggiunge colonne combinate L+R + tipo overlay
    "geo_symmetric_difference",  # combina schemi L+R
    "geo_within",           # filter-like + flag column
    "lookup",               # aggiunge colonne dal lookup table
    "melt",                 # ristruttura wide→long
    "statistics",           # aggiunge colonne statistiche
    "table_diff",           # aggiunge colonne diff
    "transpose",            # ristruttura schema
    "window_function",      # aggiunge colonna funzione finestra
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
    assert actual == 12, (
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
        transform_op="cross_join",  # passthrough-WRONG entry residua
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
