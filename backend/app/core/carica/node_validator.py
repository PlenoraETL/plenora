"""Validatore PUT-time per i nodi CARICA (Epic Canvas Validation 1.6).

Riusa la regola pura condivisa ``check_mapping`` per il sottoinsieme comune
(source-exists / dup-source / dup-dest) e copre le **regole extra di carica** che
``check_mapping`` non ha (erano in ``ColumnMapper.validate_mapping``, dead-in-prod):
≥1-attivo, max-1-geometria, match-column (UPDATE/UPSERT) — altrimenti il PUT
sotto-validerebbe carica (false-OK, viola #B).

Onora lo status (#A): se l'upstream è ``indeterminate`` (schema a monte non
risolvibile), una colonna sorgente mancante è un **warning**, non un error — la
colonna potrebbe esistere a runtime.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, List

from app.shared.execution.mapping_rules import check_mapping
from app.shared.execution.schema_types import InferredSchema
from app.shared.execution.validation_protocol import register_node_validator
from app.shared.execution.validation_types import Issue

# I node.type che rappresentano un nodo CARICA (allineato a CARICA_NODE_TYPES).
_CARICA_NODE_TYPES = ("carica", "carica_arcgis", "datasource_node")


def _normalize(raw: Any) -> SimpleNamespace:
    """Dict di mapping (JSON canvas) → oggetto duck-typed per check_mapping.

    Tollera gli alias storici source/source_column e dest/dest_column.
    """
    if not isinstance(raw, dict):
        return SimpleNamespace(source_column="", dest_column="", is_geometry=False, skip=True)
    src = (raw.get("source_column") or raw.get("source") or "").strip()
    dst = (raw.get("dest_column") or raw.get("dest") or "").strip()
    return SimpleNamespace(
        source_column=src, dest_column=dst,
        is_geometry=raw.get("is_geometry") is True,
        skip=raw.get("skip") is True,
    )


class CaricaNodeValidator:
    """``validate_node`` per i nodi CARICA."""

    def validate_node(self, node: Any, *, upstream: InferredSchema) -> List[Issue]:
        data = getattr(node, "data", None) or {}
        node_id = getattr(node, "id", "")
        raw_mappings = data.get("column_mappings") or []
        active = [m for m in (_normalize(r) for r in raw_mappings)
                  if m.source_column and m.dest_column and not m.skip]

        # Nessun mapping esplicito: a runtime carica fa auto_map → niente da
        # validare a PUT (validare ora darebbe falsi positivi).
        if not active:
            return []

        columns = {
            c["name"] for c in upstream.columns
            if isinstance(c, dict) and c.get("name")
        }
        issues = check_mapping(columns, active, node_id=node_id)

        # #A: con upstream indeterminate, "source mancante" è un warning, non error.
        if upstream.status == "indeterminate":
            issues = [
                replace(i, severity="warning") if i.code == "mapping.source_missing" else i
                for i in issues
            ]

        # Extra carica #1: al più una colonna geometria.
        geom_dests = [m.dest_column for m in active if m.is_geometry]
        if len(geom_dests) > 1:
            issues.append(Issue(
                node_id=node_id, severity="error", code="mapping.multi_geometry",
                message="Più di una colonna geometria nel mapping: " + ", ".join(geom_dests),
                suggestion={"columns": geom_dests},
            ))

        # Extra carica #2: la colonna match (UPDATE/UPSERT ArcGIS) dev'essere prodotta.
        match = (data.get("arcgis_match_column") or "").strip()
        if match and match not in {m.dest_column for m in active}:
            issues.append(Issue(
                node_id=node_id, severity="error", code="mapping.match_missing",
                message=f"La colonna match '{match}' non è prodotta dal mapping",
                suggestion={"column": match},
            ))

        return issues


def register_carica_validator() -> None:
    """Registra il validatore CARICA al composition-root (#D)."""
    validator = CaricaNodeValidator()
    for node_type in _CARICA_NODE_TYPES:
        register_node_validator(node_type, validator)


__all__ = ["CaricaNodeValidator", "register_carica_validator"]
