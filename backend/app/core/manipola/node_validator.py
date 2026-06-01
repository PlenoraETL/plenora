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

from typing import Any, List

from app.core.manipola.transforms.formula import _validate_formula_ast
from app.shared.execution.config_column_refs import (
    referenced_column_names,
    resolve_config_column_refs,
)
from app.shared.execution.schema_types import InferredSchema
from app.shared.execution.validation_protocol import register_node_validator
from app.shared.execution.validation_types import Issue

_MANIPOLA_NODE_TYPES = ("transform", "multi_join")


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

        return issues


def register_manipola_validator() -> None:
    """Registra il validatore transform al composition-root (#D)."""
    validator = ManipolaNodeValidator()
    for node_type in _MANIPOLA_NODE_TYPES:
        register_node_validator(node_type, validator)


__all__ = ["ManipolaNodeValidator", "register_manipola_validator"]
