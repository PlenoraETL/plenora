"""Ottimizzazioni memory-safe applicate agli input dei join MANIPOLA."""

from __future__ import annotations

from typing import Any, Mapping

from app.core.dataframes.frame_copy import (
    copy_frame_for_update,
    is_string_like_series,
)
from app.core.dataframes.ports import FrameLike
from app.utils.logger import get_logger


logger = get_logger(__name__)


def _node_config(node: Any) -> Mapping[str, Any]:
    direct = getattr(node, "config", None)
    if isinstance(direct, dict) and direct:
        return direct
    data = getattr(node, "data", None)
    if isinstance(data, dict) and isinstance(data.get("config"), dict):
        return data["config"]
    return {}


def project_columns_dropped_immediately_after_join(
    canvas: Any,
    join_node: Any,
    inputs: list[FrameLike],
) -> list[FrameLike]:
    """Non materializza nel merge colonne eliminate dal nodo successivo.

    L'ottimizzazione e' intenzionalmente stretta: join con un solo consumer,
    seguito direttamente da ``drop_columns``. I suffissi ``_L``/``_R`` sono
    il contratto di :func:`op_join`; il successivo drop resta un no-op sulle
    colonne gia' proiettate e il risultato osservabile non cambia.
    """
    if len(inputs) != 2 or getattr(join_node, "transform_op", None) != "join":
        return inputs

    outgoing = [edge for edge in canvas.edges if edge.source == join_node.id]
    if len(outgoing) != 1:
        return inputs
    downstream = next(
        (node for node in canvas.nodes if node.id == outgoing[0].target),
        None,
    )
    if downstream is None or getattr(downstream, "transform_op", None) != "drop_columns":
        return inputs

    dropped_output_columns = _node_config(downstream).get("columns") or []
    join_config = _node_config(join_node)
    left_keys = set(join_config.get("left_keys") or [join_config.get("left_on")])
    right_keys = set(join_config.get("right_keys") or [join_config.get("right_on")])
    left_keys.discard(None)
    right_keys.discard(None)

    left_drop = {
        str(column)[:-2]
        for column in dropped_output_columns
        if str(column).endswith("_L")
    } - left_keys
    right_drop = {
        str(column)[:-2]
        for column in dropped_output_columns
        if str(column).endswith("_R")
    } - right_keys
    left_existing = [column for column in inputs[0].columns if column in left_drop]
    right_existing = [column for column in inputs[1].columns if column in right_drop]
    if not left_existing and not right_existing:
        return inputs

    projected = list(inputs)
    if left_existing:
        projected[0] = inputs[0].drop(columns=left_existing)
    if right_existing:
        projected[1] = inputs[1].drop(columns=right_existing)
    logger.info(
        "[JOIN-PROJECTION] node=%s pruned_left=%s pruned_right=%s",
        join_node.id,
        left_existing,
        right_existing,
    )
    return projected


def compact_owned_join_string_columns(
    inputs: list[FrameLike],
    join_node: Any,
    *,
    minimum_rows: int = 10_000,
) -> list[FrameLike]:
    """Converte payload object composto solo da stringhe in Arrow string.

    Va chiamata soltanto quando l'executor possiede l'ultimo riferimento agli
    input. In tal modo i vecchi array object vengono liberati prima del merge.
    Le join key sono escluse per non alterare coercion e policy sui null.
    """
    config = _node_config(join_node)
    left_keys = set(config.get("left_keys") or [config.get("left_on")])
    right_keys = set(config.get("right_keys") or [config.get("right_on")])
    left_keys.discard(None)
    right_keys.discard(None)

    compacted: list[FrameLike] = []
    converted: list[list[str]] = []
    for frame, keys in zip(inputs, (left_keys, right_keys)):
        if len(frame) < minimum_rows:
            compacted.append(frame)
            converted.append([])
            continue
        result = copy_frame_for_update(frame)
        frame_converted: list[str] = []
        for column in result.columns:
            if column in keys or result[column].dtype != object:
                continue
            if not is_string_like_series(result[column]):
                continue
            result[column] = result[column].astype("string[pyarrow]")
            frame_converted.append(str(column))
        compacted.append(result)
        converted.append(frame_converted)

    if any(converted):
        logger.info(
            "[JOIN-ARROW] node=%s converted_left=%s converted_right=%s",
            join_node.id,
            converted[0],
            converted[1],
        )
    return compacted


__all__ = [
    "compact_owned_join_string_columns",
    "project_columns_dropped_immediately_after_join",
]
