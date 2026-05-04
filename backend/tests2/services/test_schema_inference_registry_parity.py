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
}

# Transform IDs nel registry che ALTERANO lo schema (aggiungono/rimuovono
# colonne, cambiano tipi). Il passthrough produce schema FE sbagliato.
# Drift target: ogni voce qui serve di un metodo `_schema_xxx` dedicato
# in `_infer_transform_schema`.
PASSTHROUGH_WRONG_GAP: Set[str] = {
    # BE-PHASE2-SCHEMA-INFER-002 batch 1 ha promosso (rimossi):
    #   add_row_number, uuid_generator, string_length, date_extract,
    #   geo_distance, geo_to_wkt
    "bin",                  # aggiunge colonna bin output
    "cross_join",           # combina colonne L+R
    "flatten_json",         # esplode chiavi JSON in colonne
    "geo_bounds_extractor", # aggiunge minx/miny/maxx/maxy
    "geo_count_points_in_polygons",  # aggiunge counts column
    "geo_explode",          # multiplica righe (no schema delta) — boundary
    "geo_length",           # aggiunge length column
    "geo_line_builder",     # produce geometry:line
    "geo_nearest",          # aggiunge colonne neighbour
    "geo_overlay",          # aggiunge colonne combinate L+R + tipo overlay
    "geo_perimeter",        # aggiunge perimeter column
    "geo_point_on_surface", # produce geometry:point
    "geo_polygon_builder",  # produce geometry:polygon
    "geo_symmetric_difference",  # combina schemi L+R
    "geo_vertex_count",     # aggiunge colonna count int
    "geo_voronoi",          # produce geometry:polygon
    "geo_within",           # filter-like + flag column
    "lookup",               # aggiunge colonne dal lookup table
    "melt",                 # ristruttura wide→long
    "reorder_columns",      # cambia ordine (non rilevante per FE), ma vedi nota
    "statistics",           # aggiunge colonne statistiche
    "string_extract",       # aggiunge colonna match
    "string_pad",           # passthrough? — verificare con team
    "table_diff",           # aggiunge colonne diff
    "transpose",            # ristruttura schema
    "window_function",      # aggiunge colonna funzione finestra
    "dedup_advanced",       # passthrough rows-only — vedi nota
    "geo_boundary",         # produce geometry:line
    "geo_clean_topology",   # passthrough rows-only — vedi nota
    "geo_make_valid",       # passthrough — vedi nota
}

# Transform IDs che operano solo su righe (filter-like) — il
# passthrough fallback e' funzionalmente corretto per il FE.
PASSTHROUGH_OK_GAP: Set[str] = {
    "sample",  # filter-like su righe
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
    assert actual == 30, (
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
        transform_op="bin",  # passthrough-WRONG entry residua
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
