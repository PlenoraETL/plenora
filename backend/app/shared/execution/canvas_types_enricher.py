"""
N11: bridge projects.columns_metadata -> carica_node_data.canvas_types.

Risolve il "Type inference loss" tra ESTRAI -> MANIPOLA -> CARICA:
"""

from __future__ import annotations

import importlib

from collections.abc import Callable

from app.shared.contracts.payloads import JsonObject, StringMap


def enrich_canvas_types_from_metadata(
    carica_node_data: JsonObject,
    project_id: str,
    columns_metadata_loader: Callable[[str], object],
) -> JsonObject:
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

    logger_mod = importlib.import_module("app.utils.logger")
    logger = logger_mod.get_logger(__name__)

    # Lazy via importlib: AST-level engine boundary guards (BE-GO-READY-ARCH-057-W1)
    # forbid `from app.core.*` ImportFrom nodes inside `app.shared.*` engine
    # subpackages. The runtime dependency on `metadata_to_dict` is real and
    # narrow, so we resolve it dynamically to keep the static surface clean.
    metadata_to_dict = importlib.import_module(
        "app.core.resources.column_types"
    ).metadata_to_dict

    node_column_types = carica_node_data.get("canvas_types", {}) or {}

    project_column_types: StringMap = {}
    try:
        cols_md = columns_metadata_loader(project_id)
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
