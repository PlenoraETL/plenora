"""Regola pura di validazione del column-mapping.

``check_mapping`` e' una regola condivisa pura, duck-typed sui mapping
(``.source_column`` / ``.dest_column`` / ``.skip``) → vale sia per
``ColumnMapping`` (carica) sia per ``ArcGISColumnMapping`` (connections), senza
introdurre un tipo nuovo né importare i domini.

Invocabile con input diversi (TOCTOU):
- a PUT: ``check_mapping(inferred_schema_columns, mappings)`` → Issue → 422;
- a runtime: ``check_mapping(actual_df_columns, mappings)`` → se ``error``, raise.

Vive in ``shared/execution`` (non in ``carica/``): ``connections``, ``carica`` e
l'orchestratore ``projects`` vi dipendono tutti, nessuno dipende dall'altro.
"""

from __future__ import annotations

from typing import AbstractSet, Any, List, Sequence

from app.shared.execution.validation_types import Issue


def _active(mappings: Sequence[Any]) -> List[Any]:
    return [
        m for m in mappings
        if getattr(m, "source_column", "")
        and getattr(m, "dest_column", "")
        and not getattr(m, "skip", False)
    ]


def _duplicates(values: Sequence[str]) -> List[str]:
    seen: set = set()
    dups: List[str] = []
    for v in values:
        if v in seen and v not in dups:
            dups.append(v)
        seen.add(v)
    return dups


def check_mapping(
    columns: AbstractSet[str],
    mappings: Sequence[Any],
    *,
    node_id: str = "",
) -> List[Issue]:
    """Valida un column-mapping contro l'insieme di nomi-colonna disponibili.

    Ritorna gli ``Issue`` (severity ``error``) in ordine — sorgenti duplicate,
    destinazioni duplicate, sorgenti mancanti — coerente con l'ordine dei check
    di ``ArcGISWriteMappingContract.apply`` (backward-compat sul rewire).
    """
    active = _active(mappings)
    issues: List[Issue] = []

    dup_src = _duplicates([m.source_column for m in active])
    if dup_src:
        issues.append(Issue(
            node_id=node_id, severity="error", code="mapping.dup_source",
            message="Sorgenti duplicate nel mapping: " + ", ".join(dup_src),
            suggestion={"columns": dup_src},
        ))

    dup_dst = _duplicates([m.dest_column for m in active])
    if dup_dst:
        issues.append(Issue(
            node_id=node_id, severity="error", code="mapping.dup_dest",
            message="Destinazioni duplicate nel mapping: " + ", ".join(dup_dst),
            suggestion={"columns": dup_dst},
        ))

    missing = [m.source_column for m in active if m.source_column not in columns]
    if missing:
        issues.append(Issue(
            node_id=node_id, severity="error", code="mapping.source_missing",
            message="Colonne sorgente assenti dallo schema: " + ", ".join(missing),
            suggestion={"columns": missing},
        ))

    return issues


__all__ = ["check_mapping"]
