"""Bridge project output_contract -> carica_node_data.canvas_types."""

from __future__ import annotations

import importlib

from collections.abc import Callable

from app.shared.contracts.payloads import JsonObject, StringMap


def enrich_canvas_types_from_output_contract(
    carica_node_data: JsonObject,
    project_id: str,
    output_contract_loader: Callable[[str], object],
) -> JsonObject:
    """Arricchisce in-place ``carica_node_data["canvas_types"]``.

    I tipi del contratto pubblico del progetto forniscono il default;
    eventuali ``canvas_types`` gia' presenti nel nodo restano prioritari.
    """

    logger_mod = importlib.import_module("app.utils.logger")
    logger = logger_mod.get_logger(__name__)
    column_types_from_contract = importlib.import_module(
        "app.core.output_contract"
    ).column_types_from_contract

    node_column_types = carica_node_data.get("canvas_types", {}) or {}

    project_column_types: StringMap = {}
    try:
        output_contract = output_contract_loader(project_id)
        project_column_types = column_types_from_contract(output_contract)
    except Exception as e:
        logger.warning(
            f"Lettura output_contract fallita per {project_id}: {e}. "
            "CARICA cadra' sul fallback _infer_type_from_pandas."
        )

    merged = {**project_column_types, **node_column_types}
    if merged:
        carica_node_data["canvas_types"] = merged
    return carica_node_data


__all__ = ["enrich_canvas_types_from_output_contract"]
