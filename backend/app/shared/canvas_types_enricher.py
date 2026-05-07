"""
N11: bridge projects.columns_metadata -> carica_node_data.canvas_types.

Risolve il "Type inference loss" tra ESTRAI -> MANIPOLA -> CARICA:
"""

from __future__ import annotations

from typing import Any, Dict

from app.services.project_service import ProjectService


def enrich_canvas_types_from_metadata(
    carica_node_data: Dict[str, Any],
    project_id: str,
) -> Dict[str, Any]:
    """Arricchisce in-place `carica_node_data["canvas_types"]` con i tipi
    canvas del project da `projects.columns_metadata`.

    Regola di merge: i canvas_types gia' presenti in node_data hanno
    priorita' (il frontend puo' averli impostati esplicitamente). Le
    colonne mancanti vengono fillate dai columns_metadata del project.

    Args:
        carica_node_data: dict del nodo CARICA (come arriva al task).
            Modificato in-place aggiungendo/sovrascrivendo la chiave
            "canvas_types".
        project_id: ID del project upstream (per lookup columns_metadata).

    Returns:
        Lo stesso dict (per chainability).
    """
    from app.utils.logger import get_logger
    logger = get_logger(__name__)

    from app.shared.column_types import metadata_to_dict

    node_column_types = carica_node_data.get("canvas_types", {}) or {}

    project_column_types: Dict[str, str] = {}
    try:
        cols_md = ProjectService().get_project_columns_metadata(project_id)
        project_column_types = metadata_to_dict(cols_md)
    except Exception as e:
        logger.warning(
            f"[N11] Lettura columns_metadata fallita per {project_id}: {e}. "
            f"CARICA cadra' sul fallback _infer_type_from_pandas."
        )

    # Merge: project fornisce il default, node_data sovrascrive
    # frontend autorevole se ha settato canvas_types espliciti
    merged = {**project_column_types, **node_column_types}
    if merged:
        carica_node_data["canvas_types"] = merged
    return carica_node_data
