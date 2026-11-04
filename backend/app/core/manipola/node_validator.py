"""Validatore PUT-time per i nodi transform (manipola).

Due check, entrambi zero-drift (riusano la logica del runtime):
1. **ref-colonna del config risolvono** contro lo schema upstream — via
   ``config_column_refs`` (la STESSA risoluzione id→nome che usa
   ``schema_inference`` a runtime → PUT e runtime non divergono). È il deliverable
   ("colonna referenziata che non esiste -> 422") per filter/select/
   group_by/join-keys/aggregations.
2. **safety della formula** — via ``_validate_formula_ast`` (la stessa gate usata
   prima di ``eval`` a runtime). I *ref dentro la formula* NON sono validati qui:
   i Name mescolano colonne e funzioni pandas-eval e flaggarli darebbe falsi
   positivi.

Onora lo status: con upstream ``indeterminate`` un ref mancante è ``warning``
(può esistere a runtime), non ``error``.
"""

from __future__ import annotations

from app.shared.execution.schema_column_plans import aggregation_output_type

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, List, Mapping

from app.core.manipola.transforms.formula import _validate_formula_ast
from app.shared.execution.config_column_refs import (
    referenced_column_names,
    resolve_config_column_refs,
)
from app.shared.execution.column_selection import select_column_plan
from app.shared.execution.node_fields import node_field
from app.shared.execution.registry_types import require_known_param_type, parameter_value
from app.shared.execution.schema_types import InferredSchema
from app.shared.execution.validation_protocol import register_node_validator
from app.shared.execution.validation_types import Issue

_MANIPOLA_NODE_TYPES = ("transform", "multi_join", "result")
_SELECTION_FIELDS = {("transform", "select_columns"): "columns", ("result", None): "output_columns"}

_REGISTRY_PATH = Path(__file__).resolve().parent / "transforms_registry.json"


@lru_cache(maxsize=1)
def _transforms_registry() -> Mapping[str, Any]:
    """Mappa op-id -> definizione del registry (`data['transforms']`).

    STESSA fonte di ``PluginExecutor._carica_registry`` → no-drift. Se il registry
    non è leggibile ritorna vuoto → i check basati su di esso vengono saltati."""
    try:
        data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        return data.get("transforms") or {}
    except Exception:
        return {}


def _valid_transform_ops() -> frozenset:
    """Op-id che il runtime sa eseguire (chiavi di ``transforms``)."""
    return frozenset(_transforms_registry().keys())


def _is_empty_param_value(val: Any) -> bool:
    """Param vuoto, stessa nozione del validatore FE (T2)."""
    return val is None or val == "" or (isinstance(val, (list, dict)) and len(val) == 0)


def _param_value_for_required_check(transform_op: str, param_id: str, config: Mapping[str, Any]) -> Any:
    if transform_op == "filter" and config.get("conditions") is not None and param_id in {"column", "operator", "value"}:
        return True
    if transform_op == "formula" and config.get("items") is not None and param_id in {"new_column", "formula"}:
        return True
    return parameter_value(config, param_id)


class ManipolaNodeValidator:
    """``validate_node`` per i nodi transform."""

    def validate_node(self, node: Any, *, upstream: InferredSchema) -> List[Issue]:
        node_id = getattr(node, "id", "")
        data = getattr(node, "data", None) or {}
        config = getattr(node, "config", None) or data.get("config") or {}
        if not isinstance(config, dict):
            config = {}

        issues: List[Issue] = []

        # 0. Operazione selezionata? (porting fedele TRANSFORM_OP_MISSING)
        transform_op = getattr(node, "transform_op", None) or data.get("transform_op")
        node_type = getattr(node, "type", "transform")
        selection_field = _SELECTION_FIELDS.get((node_type, transform_op if node_type != "result" else None))
        if selection_field:
            columns = (node_field(node, selection_field) if node_type == "result" else config.get(selection_field))
            if columns is None and node_type == "result":
                return issues
            upstream_cols = [c for c in upstream.columns if isinstance(c, dict)]
            names = {c["name"] for c in upstream_cols if c.get("name")}
            id_to_name = {
                str(c.get("id") or c.get("column_id")): c["name"]
                for c in upstream_cols if (c.get("id") or c.get("column_id")) and c.get("name")
            }
            resolved = resolve_config_column_refs({"columns": columns}, id_to_name)
            try:
                select_column_plan(list(names), resolved["columns"], skip_missing=True)
            except ValueError as exc:
                issues.append(Issue(
                    node_id=node_id, severity="error", code="columns.invalid",
                    message=str(exc), suggestion=None,
                ))
            else:
                for missing in sorted(referenced_column_names(resolved) - names):
                    issues.append(Issue(
                        node_id=node_id, severity="warning", code="column.missing",
                        message=f"Colonna '{missing}' assente a monte, ignorata",
                        suggestion={"column": missing},
                    ))
            if node_type == "result":
                return issues
        if not transform_op:
            issues.append(Issue(
                node_id=node_id, severity="error", code="TRANSFORM_OP_MISSING",
                message="Nodo trasformazione senza operazione selezionata.",
                suggestion=None,
            ))
        else:
            # #B: op orfana — presente nel Literal ma assente dal registry eseguibile
            # (es. legacy merge/query_db) → passerebbe il PUT ma fallirebbe a runtime.
            valid_ops = _valid_transform_ops()
            if valid_ops and transform_op not in valid_ops:
                issues.append(Issue(
                    node_id=node_id, severity="error", code="TRANSFORM_OP_UNKNOWN",
                    message=f"Operazione '{transform_op}' non riconosciuta dal motore di trasformazione.",
                    suggestion={"op": transform_op},
                ))

        upstream_cols = [c for c in upstream.columns if isinstance(c, dict)]
        names = {c["name"] for c in upstream_cols if c.get("name")}

        # 1. ref-colonna del config → risolvi id→nome (come il runtime) e verifica.
        id_to_name = {
            str(c["id"]): c["name"]
            for c in upstream_cols if c.get("id") and c.get("name")
        }
        ref_config = dict(config)
        if transform_op == "filter" and config.get("conditions") is not None:
            for key in ("column", "column_column_id", "operator", "value"):
                ref_config.pop(key, None)
        resolved = resolve_config_column_refs(ref_config, id_to_name)
        # #A: upstream indeterminate → ref mancante = warning, non error.
        severity = "warning" if upstream.names_status == "indeterminate" else "error"
        # Selection references were already checked above with skip-missing semantics.
        references = set() if transform_op == "select_columns" else referenced_column_names(resolved)
        nested = resolved.get("conditions") if transform_op == "filter" else []
        if isinstance(nested, list):
            field = "name" if transform_op == "select_columns" else "column"
            references.update(item[field] for item in nested if isinstance(item, dict) and isinstance(item.get(field), str))
        for key in ("left_columns", "right_columns"):
            selected = resolved.get(key)
            if isinstance(selected, list):
                references.update(name for name in selected if isinstance(name, str))
        for missing in sorted(references - names):
            issues.append(Issue(
                node_id=node_id, severity=severity, code="column.missing",
                message=f"Colonna '{missing}' referenziata dal transform ma assente a monte",
                suggestion={"column": missing},
            ))

        if transform_op == "aggregate":
            by_name = {c["name"]: c for c in upstream_cols if c.get("name")}
            for aggregation in resolved.get("aggregations") or []:
                column = by_name.get(aggregation.get("column"))
                if column is None:
                    continue
                try:
                    aggregation_output_type(aggregation.get("function", "count"), column.get("type", "str"))
                except TypeError as exc:
                    issues.append(Issue(
                        node_id=node_id, severity="warning" if column.get("optional") else "error",
                        code="aggregation.unsupported", message=str(exc), suggestion=None,
                    ))

        # 2. safety della formula (riusa la gate AST del runtime).
        formulas = [config.get("formula")]
        if transform_op == "formula" and config.get("items") is not None:
            formulas = [item.get("formula") for item in config["items"] if isinstance(item, dict)]
        for formula in formulas:
            if isinstance(formula, str) and formula.strip():
                try:
                    _validate_formula_ast(formula)
                except ValueError as exc:
                    issues.append(Issue(
                        node_id=node_id, severity="error", code="formula.invalid",
                        message=f"Formula non valida: {exc}", suggestion=None,
                    ))

        # 3. completezza/coerenza dei parametri rispetto al registry (porting
        #    fedele di TRANSFORM_PARAM_REQUIRED [T2] e TRANSFORM_GEOMETRY_TYPE_INVALID
        #    [T4] dal validatore FE). Le definizioni param vengono dalla STESSA
        #    fonte del runtime (transforms_registry.json) → no-drift.
        op_def = _transforms_registry().get(transform_op) if transform_op else None
        if op_def:
            col_type = {c["name"]: (c.get("type") or "") for c in upstream_cols if c.get("name")}
            for param in (op_def.get("parameters") or []):
                require_known_param_type(param.get("type"))
                pid = param.get("id")
                if not pid:
                    continue
                val = _param_value_for_required_check(transform_op, pid, config)
                # T2: parametro obbligatorio non configurato → errore deterministico
                # (completezza di config, non dipende dai dati a monte).
                if param.get("required") and _is_empty_param_value(val):
                    label = param.get("label") or pid
                    issues.append(Issue(
                        node_id=node_id, severity="error", code="TRANSFORM_PARAM_REQUIRED",
                        message=f"Parametro obbligatorio '{label}' non configurato.",
                        suggestion={"param": pid},
                    ))
                    continue
                # T4: param geometry_column che punta a una colonna nota NON di tipo
                # geometry → errore. #A: con upstream indeterminate i tipi possono
                # essere parziali → warning (mai falso blocco).
                if param.get("type") == "geometry_column" and not _is_empty_param_value(val):
                    resolved_name = id_to_name.get(str(val), val)
                    if resolved_name in col_type and not str(col_type[resolved_name]).startswith("geometry"):
                        geom_sev = "warning" if upstream.types_status == "indeterminate" else "error"
                        issues.append(Issue(
                            node_id=node_id, severity=geom_sev, code="TRANSFORM_GEOMETRY_TYPE_INVALID",
                            message=f"La colonna '{resolved_name}' non è di tipo geometry.",
                            suggestion={"param": pid, "column": resolved_name},
                        ))

        return issues


def register_manipola_validator() -> None:
    """Registra il validatore transform al composition-root (#D)."""
    validator = ManipolaNodeValidator()
    for node_type in _MANIPOLA_NODE_TYPES:
        register_node_validator(node_type, validator)


__all__ = ["ManipolaNodeValidator", "register_manipola_validator"]
