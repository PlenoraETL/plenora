"""A3 P1+P2: Valida di un passo Manipola = esecuzione a vuoto della stessa operazione.

Guardia di enumerazione sul registry reale (71 operazioni):

(a) ogni operazione sta in esattamente una tabella: ``CASES`` (almeno un
    negativo vero: l'esecuzione con righe lo rifiuta, Valida a vuoto lo rifiuta
    con lo stesso messaggio), ``VUOTO_NON_GARANTIBILE`` (tabella del runtime,
    con motivo) o ``SENZA_VINCOLI`` (con motivo). Operazione nuova o mancante
    → fallimento;
(b) per TUTTE: parametro obbligatorio mancante, parametro sconosciuto,
    colonna inesistente. Se l'esecuzione con righe rifiuta → Valida rifiuta
    (stesso messaggio quando l'esecuzione a vuoto lo vede); se l'esecuzione
    produce un no-op silenzioso (uscita == ingresso) → Valida dà ERRORE; un
    avviso è ammesso solo dove il runtime applica il default del registry e
    dà lo stesso risultato del default; gli unici errori su ciò che il runtime
    accetta sono le regole dichiarate solo-Valida (``DECLARATIVE_ERROR_CODES``);
(c) per tutte le non dichiarate: configurazioni valide (casi di parità A1)
    → nessun errore di Valida, né sullo schema esatto né sul canvas inferito.
"""

from __future__ import annotations

import time

import pytest

from app.core.manipola.canvas_schema_runtime import build_columns_with_types
from app.core.manipola.dry_run import CON_IO_ESTERNO, VUOTO_NON_GARANTIBILE, dry_run_transform
from app.core.manipola.node_validator import ManipolaNodeValidator, register_manipola_validator
from app.core.manipola.plugin_executor import get_executor
from app.core.projects.canvas_models import Node
from app.shared.execution.schema_types import InferredSchema
from tests2.contracts.inference_runtime_cases import CASES as VALID, EXTRA
from tests2.contracts.test_inference_runtime_parity import frames

pytestmark = [pytest.mark.unit, pytest.mark.contract]

REGISTRY = get_executor().registry
MISSING = object()  # nel caso negativo: togli il parametro dalla configurazione valida
TWO = ["uno", "due"]  # nome di uscita multiplo per una colonna singola
CRS = "non-un-crs"

# (a) Negativi veri: sovrascritture della configurazione valida A1.
CASES: dict[str, list[dict]] = {
    "select_columns": [{"columns": [{"alias": "x"}]}],
    "join": [{"left_keys": ["assente"]}, {"name_conflict": "ignora"}, {"how": "diagonale"}],
    "filter": [{"column": "assente"}, {"conditions": [{"column": "a"}]}],
    "formula": [{"formula": "assente * 2"}, {"formula": "__import__('os')"}],
    "concat_columns": [{"output_column": TWO}],
    "aggregate": [{"aggregations": ["a"]}],
    "sort": [{"ascending": None}],
    "distinct": [{"subset": {"a": 1}}],
    "rename": [{"renames": ["a"]}],
    "drop_columns": [{"columns": {"a": 1}}],
    "fill_na": [{"value": "testo"}],
    "split_column": [{"max_splits": "due"}],
    "sjoin": [{"how": "diagonale"}],
    "geo_buffer": [{"metric_crs": CRS}, {"output_crs": CRS}],
    "geo_centroid": [{"metric_crs": CRS}],
    "geo_area": [{"metric_crs": CRS}],
    "geo_dissolve": [{"aggfunc": "mediana_inesistente"}],
    "geo_simplify": [{"tolerance": 0}],
    "geo_convex_hull": [{"output_column": TWO}],
    "geo_reproject": [{"target_crs": CRS}],
    "geo_envelope": [{"output_column": TWO}],
    "md5_hash": [{"output_column": TWO}],
    "mask_data": [{"maskings": ["a"]}],
    "type_cast": [{"column": ["a"]}],
    "geo_from_coords": [{"crs": CRS}],
    "conditional": [{"conditions": ["a"]}],
    "text_normalize": [{"columns": {"s": 1}}],
    "pivot": [{"aggr_func": 1}],
    "table_diff": [{"separator": 1}],
    "geo_distance": [{"metric_crs": CRS}],
    "geo_length": [{"metric_crs": CRS}],
    "geo_perimeter": [{"metric_crs": CRS}],
    "geo_point_on_surface": [{"output_column": TWO}],
    "geo_boundary": [{"output_column": TWO}],
    "geo_to_wkt": [{"wkt_column": TWO}],
    "melt": [{"id_columns": MISSING}],
    "add_row_number": [{"start": "uno"}],
    "date_extract": [{"parts": 1}],
    "lookup": [{"unmatched": "ignora"}, {"mapping": "non json"}],
    "bin": [{"labels": "basso,alto"}],
    "flatten_json": [{"output_columns": 1}],
    "geo_clean_topology": [{"snap_tolerance": "molta"}],
    "statistics": [{"stats": 1}],
    "string_extract": [{"pattern": "senza_gruppi"}, {"pattern": MISSING}],
    "geo_vertex_count": [{"output_column": TWO}],
    "geo_line_builder": [{"group_column": "assente"}],
    "geo_polygon_builder": [{"group_column": "assente"}],
}

_GEOMETRY_ONLY = ("Parametri solo colonne geometriche: il runtime usa la geometria attiva del "
                  "GeoDataFrame (to_geodataframe lo restituisce invariato), nessuna configurazione rifiutata")
SENZA_VINCOLI: dict[str, str] = {
    "concat": "Unico parametro booleano (ignore_index), convertito dalla normalizzazione: nessun valore rifiutato",
    "cross_join": "Nessun parametro",
    "geo_clip": _GEOMETRY_ONLY,
    "geo_intersection": _GEOMETRY_ONLY,
    "geo_union": _GEOMETRY_ONLY,
    "geo_difference": _GEOMETRY_ONLY,
    "geo_symmetric_difference": _GEOMETRY_ONLY,
    "geo_within": _GEOMETRY_ONLY,
    "geo_explode": _GEOMETRY_ONLY,
    "geo_make_valid": _GEOMETRY_ONLY,
    "geo_voronoi": _GEOMETRY_ONLY,
    "geo_bounds_extractor": _GEOMETRY_ONLY + "; il prefisso è testo libero",
    "geo_overlay": _GEOMETRY_ONLY + "; how non riconosciuto ricondotto a 'intersection' dal runtime",
}

# Operazioni il cui unico rifiuto di configurazione (con righe) non si vede a
# vuoto senza cambiare l'esecuzione di HEAD: lo copre una regola solo-Valida.
SOLO_REGOLA_VALIDA: dict[str, tuple[dict, str, str]] = {
    "replace": ({"old_value": MISSING}, "TRANSFORM_PARAM_REQUIRED",
                "Con righe pandas rifiuta replace(None, ...); su zero righe HEAD restituisce l'ingresso "
                "(regola madre: l'esecuzione non cambia). Lo rifiuta il parametro obbligatorio mancante"),
}

COLUMN_PARAM_TYPES = {"column", "columns", "geometry_column"}

# Regole del validatore che NON sono vincoli di esecuzione (schema a monte):
# possono rifiutare ciò che il runtime accetta, per scelta dichiarata.
DECLARATIVE_ERROR_CODES = {
    "rename.duplicate": "P5: la rinomina non deve produrre nomi duplicati (il salvataggio li suffisserebbe)",
    "aggregation.unsupported": "Tabella tipi/aggregazioni generata dall'esecuzione con righe",
    "TRANSFORM_GEOMETRY_TYPE_INVALID": "Parametro geometria su colonna nota non geometrica",
    "TRANSFORM_PARAM_REQUIRED": "Parametro obbligatorio mancante: il runtime fa un no-op o altro dall'intenzione (HEAD)",
    "column.missing": "Colonna configurata assente: il runtime la ignora in silenzio (HEAD)",
}


def valid_config(op: str, raw: dict | None = None) -> dict:
    config = dict(VALID[op] if raw is None else raw)
    for param in REGISTRY[op].get("parameters", []):
        if "geom" in param["id"] and param["id"] != "keep_geom_type":
            config.setdefault(param["id"], "geometry")
    return config


def schemas_of(inputs: list) -> list[InferredSchema]:
    """Schema esatto dei frame d'esempio, CRS compreso (``geometry:point:<srid>``)."""
    out = []
    for frame in inputs:
        crs = getattr(frame, "crs", None)
        columns = build_columns_with_types(frame, {})
        for column in columns:
            if column["type"] == "geometry" and crs is not None:
                column["type"] = f"geometry:point:{crs.to_epsg()}"
        out.append(InferredSchema(columns=columns))
    return out


def node_for(op: str, config: dict) -> Node:
    return Node.model_validate({"id": "t", "type": "transform", "position": {"x": 0, "y": 0},
                                "transform_op": op, "config": config})


def runtime_error(op: str, inputs: list, config: dict) -> str | None:
    """Esecuzione reale con righe, dispatcher dell'esecutore Manipola."""
    from app.core.manipola.executor import ManipolaExecutor
    try:
        ManipolaExecutor(project_service=None)._esegui_trasformazione(node_for(op, config), inputs)
    except Exception as error:  # noqa: BLE001 - il confronto riguarda qualunque rifiuto
        return str(error)
    return None


def valida_errors(op: str, inputs: list, config: dict, *, declarative: bool = True) -> list[str]:
    schemas = schemas_of(inputs)
    upstream = InferredSchema(columns=[col for schema in schemas for col in schema.columns])
    issues = ManipolaNodeValidator().validate_node(node_for(op, config), upstream=upstream, inputs=schemas)
    return [issue.message for issue in issues if issue.severity == "error"
            and (declarative or issue.code not in DECLARATIVE_ERROR_CODES)]


def valida_codes(op: str, inputs: list, config: dict, severity: str) -> set[str]:
    schemas = schemas_of(inputs)
    upstream = InferredSchema(columns=[col for schema in schemas for col in schema.columns])
    issues = ManipolaNodeValidator().validate_node(node_for(op, config), upstream=upstream, inputs=schemas)
    return {issue.code for issue in issues if issue.severity == severity}


def runtime_result(op: str, inputs: list, config: dict):
    from app.core.manipola.executor import ManipolaExecutor
    return ManipolaExecutor(project_service=None)._esegui_trasformazione(node_for(op, config), inputs)


def same_frame(left, right) -> bool:
    return (list(left.columns) == list(right.columns) and [str(t) for t in left.dtypes] == [str(t) for t in right.dtypes]
            and left.astype(str).reset_index(drop=True).equals(right.astype(str).reset_index(drop=True)))


def apply(config: dict, override: dict) -> dict:
    out = dict(config)
    for key, value in override.items():
        if value is MISSING:
            out.pop(key, None)
        else:
            out[key] = value
    return out


# (a) -------------------------------------------------------------------------


def test_every_registry_operation_is_classified_exactly_once():
    tables = [set(CASES), set(VUOTO_NON_GARANTIBILE), set(SENZA_VINCOLI), set(SOLO_REGOLA_VALIDA)]
    assert sum(len(table) for table in tables) == len(set().union(*tables)), "operazione in più tabelle"
    assert set().union(*tables) == set(REGISTRY), (
        f"non classificate: {sorted(set(REGISTRY) - set().union(*tables))}; "
        f"fuori registry: {sorted(set().union(*tables) - set(REGISTRY))}")
    assert len(REGISTRY) == 71
    assert all(CASES[op] for op in CASES)
    assert all(reason.strip() for reason in [*VUOTO_NON_GARANTIBILE.values(), *SENZA_VINCOLI.values(), *CON_IO_ESTERNO.values()])
    assert not set(CON_IO_ESTERNO) - set(REGISTRY)


@pytest.mark.parametrize("op,override", [(op, item) for op, items in CASES.items() for item in items])
def test_true_negative_has_same_reason(op, override):
    inputs = frames(op, False)
    config = apply(valid_config(op), override)
    message = runtime_error(op, inputs, config)
    assert message is not None, "il caso negativo deve essere rifiutato dall'esecuzione con righe"
    assert message in valida_errors(op, inputs, config)


@pytest.mark.parametrize("op", sorted(SOLO_REGOLA_VALIDA))
def test_validation_only_rule_covers_runtime_refusal(op):
    override, code, reason = SOLO_REGOLA_VALIDA[op]
    inputs = frames(op, False)
    config = apply(valid_config(op), override)
    assert reason and runtime_error(op, inputs, config) is not None
    assert code in valida_codes(op, inputs, config, "error")


@pytest.mark.parametrize("op", sorted(set(VUOTO_NON_GARANTIBILE) | set(CON_IO_ESTERNO)))
def test_declared_operations_are_not_guaranteed_never_errors(op):
    inputs = frames(op, False)
    node = node_for(op, {"zz_sconosciuto": object()})
    outcome = dry_run_transform(node, schemas_of(inputs), op)
    assert outcome.verdict == "not_guaranteed" and outcome.message


# (b) -------------------------------------------------------------------------


def generic_variants(op: str) -> list[tuple[str, dict]]:
    config = valid_config(op)
    variants = [("sconosciuto", {**config, "zz_sconosciuto": 1})]
    for param in REGISTRY[op].get("parameters", []):
        if param.get("required"):
            variants.append((f"-{param['id']}", apply(config, {param["id"]: MISSING})))
            # Presente ma vuoto, come lo manda l'interfaccia svuotando un campo:
            # il runtime non applica il default (solo con la chiave assente).
            for empty in ("", None):
                variants.append((f"={param['id']}:{empty!r}", {**config, param["id"]: empty}))
        if param.get("type") in COLUMN_PARAM_TYPES:
            missing = ["assente"] if param["type"] == "columns" else "assente"
            variants.append((f"?{param['id']}", {**config, param["id"]: missing}))
    return variants


def _param(op: str, name: str) -> dict:
    pid = name[1:].split(":", 1)[0]
    return next(param for param in REGISTRY[op].get("parameters", []) if param["id"] == pid)


@pytest.mark.parametrize("op,name,config", [(op, name, config) for op in REGISTRY for name, config in generic_variants(op)])
def test_generic_variants_same_outcome(op, name, config):
    inputs = frames(op, False)
    message = runtime_error(op, inputs, config)
    errors = valida_errors(op, inputs, config)
    if message is not None:
        assert errors, f"il runtime rifiuta ({message}), Valida no"
        if dry_run_transform(node_for(op, config), schemas_of(inputs), op).verdict == "rejected":
            assert message in errors, f"il runtime rifiuta ({message}), Valida con altro messaggio: {errors}"
        return
    false_positives = valida_codes(op, inputs, config, "error") - set(DECLARATIVE_ERROR_CODES)
    assert not false_positives, f"falso positivo: il runtime accetta, Valida rifiuta: {errors}"
    if name == "sconosciuto":
        return
    if errors:
        return
    # Nessun errore: ammesso unicamente se il runtime applica il default del
    # registry (stesso risultato del default esplicito). Altrimenti l'uscita è
    # un no-op silenzioso o un risultato diverso dall'intenzione.
    result = runtime_result(op, inputs, config)
    kind = "no-op silenzioso" if same_frame(result, inputs[0]) else "risultato senza il parametro"
    param = _param(op, name)
    assert name.startswith("-") and param.get("default") not in (None, "", [], {}), (
        f"{kind} ({name}): Valida deve dare errore")
    expected = runtime_result(op, inputs, {**config, param["id"]: param["default"]})
    assert same_frame(result, expected), f"{kind} ({name}): avviso senza che il runtime applichi il default"


# (c) -------------------------------------------------------------------------


POSITIVE = [(op, raw) for op, raw in list(VALID.items()) + EXTRA if op not in VUOTO_NON_GARANTIBILE and op not in CON_IO_ESTERNO]


@pytest.mark.parametrize("op,raw", POSITIVE)
def test_valid_configuration_has_no_false_positive(op, raw):
    inputs = frames(op, False)
    config = valid_config(op, raw)
    if runtime_error(op, inputs, config) is not None:
        pytest.skip("configurazione A1 rifiutata anche con righe: non è un caso positivo")
    assert valida_errors(op, inputs, config, declarative=False) == []
    outcome = dry_run_transform(node_for(op, config), schemas_of(inputs), op)
    assert outcome.verdict == "verified", outcome.message


@pytest.mark.parametrize("op,raw", POSITIVE)
def test_valid_configuration_through_inferred_canvas(op, raw):
    """Stesso caso positivo dal canvas: schema inferito, maniglie, validate_canvas."""
    from app.core.projects.canvas_validation import validate_canvas
    from tests2.contracts.test_a1_correction import canvas_frames
    inputs = frames(op, False)
    config = valid_config(op, raw)
    if runtime_error(op, inputs, config) is not None:
        pytest.skip("configurazione A1 rifiutata anche con righe: non è un caso positivo")
    register_manipola_validator()
    report = validate_canvas(canvas_frames(op, inputs, config), project_id="p", project_type="manipola")
    assert [issue.message for issue in report.issues if issue.node_id == "t" and issue.severity == "error"
            and issue.code not in DECLARATIVE_ERROR_CODES] == []


def test_dry_run_cost_on_large_canvas():
    """60 passi in catena: l'esecuzione a vuoto resta economica (soglia larga)."""
    from app.core.projects.canvas_validation import validate_canvas
    from tests2.contracts.test_inference_runtime_parity import canvas_of
    register_manipola_validator()
    steps = [("formula", {"new_column": f"c{i}", "formula": "a * 2"}) if i % 3 == 0 else
             ("filter", {"column": "a", "value": "1"}) if i % 3 == 1 else
             ("sort", {"columns": "a"}) for i in range(60)]
    nodes = [{"id": "s", "type": "file_source", "data": {"columns": [{"name": "a", "type": "int"}, {"name": "s", "type": "str"}]}}]
    nodes += [{"id": f"t{i}", "type": "transform", "transform_op": op, "config": config} for i, (op, config) in enumerate(steps)]
    nodes.append({"id": "r", "type": "result"})
    canvas = canvas_of(nodes, [{"id": f"e{i}", "source": a["id"], "target": b["id"]} for i, (a, b) in enumerate(zip(nodes, nodes[1:]))])
    started = time.perf_counter()
    report = validate_canvas(canvas, project_id="p", project_type="manipola")
    elapsed = time.perf_counter() - started
    assert not report.has_errors, report.issues
    assert elapsed < 30
