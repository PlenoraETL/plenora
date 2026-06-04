"""Validatore PUT-time per i nodi transform (manipola) — Epic Canvas Validation 1.6.

Due check, entrambi zero-drift (riusano la logica del runtime):
1. **ref-colonna del config risolvono** contro lo schema upstream — via
   ``config_column_refs`` (la STESSA risoluzione id→nome che usa
   ``schema_inference`` a runtime → PUT e runtime non divergono). È il deliverable
   headline di #1 ("colonna referenziata che non esiste → 422") per filter/select/
   group_by/join-keys/aggregations.
2. **safety della formula** — via ``_validate_formula_ast`` (la stessa gate usata
   prima di ``eval`` a runtime). I *ref dentro la formula* NON sono validati qui:
   i Name mescolano colonne e funzioni pandas-eval e flaggarli darebbe falsi
   positivi (vedi FINDING 1.6c).

Onora lo status (#A): con upstream ``indeterminate`` un ref mancante è ``warning``
(può esistere a runtime), non ``error``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, List, Mapping

from app.core.manipola.transforms.formula import _validate_formula_ast
from app.shared.execution.config_column_refs import (
    referenced_column_names,
    resolve_config_column_refs,
)
from app.shared.execution.schema_types import InferredSchema
from app.shared.execution.validation_protocol import register_node_validator
from app.shared.execution.validation_types import Issue

_MANIPOLA_NODE_TYPES = ("transform", "multi_join")

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
        resolved = resolve_config_column_refs(config, id_to_name)
        # #A: upstream indeterminate → ref mancante = warning, non error.
        severity = "warning" if upstream.status == "indeterminate" else "error"
        for missing in sorted(referenced_column_names(resolved) - names):
            issues.append(Issue(
                node_id=node_id, severity=severity, code="column.missing",
                message=f"Colonna '{missing}' referenziata dal transform ma assente a monte",
                suggestion={"column": missing},
            ))

        # 2. safety della formula (riusa la gate AST del runtime).
        formula = config.get("formula")
        if isinstance(formula, str) and formula.strip():
            try:
                _validate_formula_ast(formula)
            except ValueError as exc:
                issues.append(Issue(
                    node_id=node_id, severity="error", code="formula.invalid",
                    message=f"Formula non valida: {exc}",
                    suggestion=None,
                ))

        # 3. completezza/coerenza dei parametri rispetto al registry (porting
        #    fedele di TRANSFORM_PARAM_REQUIRED [T2] e TRANSFORM_GEOMETRY_TYPE_INVALID
        #    [T4] dal validatore FE). Le definizioni param vengono dalla STESSA
        #    fonte del runtime (transforms_registry.json) → no-drift.
        op_def = _transforms_registry().get(transform_op) if transform_op else None
        if op_def:
            col_type = {c["name"]: (c.get("type") or "") for c in upstream_cols if c.get("name")}
            for param in (op_def.get("parameters") or []):
                pid = param.get("id")
                if not pid:
                    continue
                val = config.get(pid)
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
                        geom_sev = "warning" if upstream.status == "indeterminate" else "error"
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
