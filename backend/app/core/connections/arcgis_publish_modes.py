"""Generic ArcGIS publish-mode primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol

from app.core.connections.arcgis_sql_helpers import chunked, quote_sql_value
from app.core.dataframes.values import is_null_scalar
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import (
    ArcGISReadProviderProtocol,
    ArcGISWriteProviderProtocol,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ArcGISPublishModeProviderProtocol(
    ArcGISReadProviderProtocol,
    ArcGISWriteProviderProtocol,
    Protocol,
):
    """Provider capabilities required by existing-service publish modes."""


@dataclass
class ArcGISPublishModeLayerResult:
    """Generic per-layer outcome for append/upsert/update publish modes."""

    added_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    skipped_count: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def primary_count(self) -> int:
        return self.updated_count if self.updated_count else self.added_count


def publish_existing_layer_mode(
    client: ArcGISPublishModeProviderProtocol,
    conn: ArcGISConnectionInfo,
    layer_url: str,
    gdf: object,
    mode: str,
    match_col: Optional[str],
    target_srid: int,
) -> ArcGISPublishModeLayerResult:
    """Apply one existing-service publish mode to one ArcGIS layer."""
    outcome = ArcGISPublishModeLayerResult()

    if mode == "update":
        _update_existing_layer(
            client,
            conn,
            layer_url,
            gdf,
            match_col,
            target_srid,
            outcome,
        )
    elif mode == "upsert":
        _delete_existing_matches_for_upsert(
            client,
            conn,
            layer_url,
            gdf,
            match_col,
            outcome,
        )
    elif mode != "append":
        outcome.errors.append(f"mode non supportata: {mode}")
        return outcome

    if mode in ("append", "upsert"):
        _add_existing_layer_features(
            client,
            conn,
            layer_url,
            gdf,
            mode,
            target_srid,
            outcome,
        )

    return outcome


def _update_existing_layer(
    client: ArcGISPublishModeProviderProtocol,
    conn: ArcGISConnectionInfo,
    layer_url: str,
    gdf: object,
    match_col: Optional[str],
    target_srid: int,
    outcome: ArcGISPublishModeLayerResult,
) -> None:
    strict_fail = False
    if not match_col or match_col not in gdf.columns:
        outcome.errors.append(f"match column '{match_col}' assente nel GDB")
        strict_fail = True
    else:
        null_count = int(gdf[match_col].isna().sum())
        if null_count > 0:
            outcome.errors.append(
                f"{null_count} feature con '{match_col}' NULL - "
                "update strict richiede chiavi valorizzate"
            )
            strict_fail = True

        dup_count = int(
            gdf[match_col].duplicated(keep=False).sum()
            - gdf[match_col].isna().sum()
        )
        if dup_count > 0:
            outcome.errors.append(
                f"{dup_count} feature con '{match_col}' duplicato - "
                "update strict non ammette chiavi duplicate"
            )
            strict_fail = True

    if strict_fail:
        outcome.skipped_count = int(len(gdf))
        return

    values = gdf[match_col].dropna().unique().tolist()
    oid_by_key: Dict[str, int] = {}
    if values:
        oid_by_key, query_errors = client.query_oid_by_key(
            conn,
            layer_url,
            match_col,
            values,
        )
        if query_errors:
            outcome.errors.extend(query_errors)
            outcome.skipped_count = int(len(gdf))
            return

    unmatched = [value for value in values if str(value) not in oid_by_key]
    if unmatched:
        preview = ", ".join(str(value) for value in unmatched[:5])
        extra = f" (+{len(unmatched) - 5} altri)" if len(unmatched) > 5 else ""
        outcome.errors.append(
            f"{len(unmatched)} valori di '{match_col}' senza match sul FS "
            f"[{preview}{extra}] - update strict richiede che ogni feature esista gia'"
        )
        outcome.skipped_count = int(len(gdf))
        return

    gdf_valid = gdf[
        gdf[match_col].apply(
            lambda value: value is not None
            and not is_null_scalar(value)
            and str(value) in oid_by_key
        )
    ]
    missing_rows = int(len(gdf) - len(gdf_valid))
    if missing_rows > 0:
        outcome.errors.append(
            f"{missing_rows} righe con match_col NULL o non risolvibile - "
            "inconsistenza con strict check"
        )
        outcome.skipped_count = int(len(gdf))
        return

    if len(gdf_valid) == 0:
        return

    try:
        update_features = client.dataframe_to_esri_features(
            gdf_valid,
            "geometry",
            target_srid,
            operation="append",
        )
        keys_in_order = [str(value) for value in gdf_valid[match_col].tolist()]
        missing_oid = 0
        for index, feature in enumerate(update_features):
            if index >= len(keys_in_order):
                break
            oid = oid_by_key.get(keys_in_order[index])
            if oid is None:
                missing_oid += 1
                continue
            feature.setdefault("attributes", {})["OBJECTID"] = oid
        if missing_oid > 0:
            outcome.errors.append(f"{missing_oid} feature senza OBJECTID post-resolve")
            return

        update_result = client.update_features(conn, layer_url, update_features)
        outcome.updated_count = int(update_result.get("updated_count", 0) or 0)
        if update_result.get("errors"):
            outcome.errors.extend(update_result["errors"])
    except Exception as exc:
        logger.error(f"update_features: {exc}", exc_info=True)
        outcome.errors.append(f"update_features: {exc}")


def _delete_existing_matches_for_upsert(
    client: ArcGISPublishModeProviderProtocol,
    conn: ArcGISConnectionInfo,
    layer_url: str,
    gdf: object,
    match_col: Optional[str],
    outcome: ArcGISPublishModeLayerResult,
) -> None:
    if not match_col:
        outcome.errors.append("nessuna match column (skip)")
        return
    if match_col not in gdf.columns:
        outcome.errors.append(f"colonna '{match_col}' assente nel GDB (skip)")
        return

    values = gdf[match_col].dropna().unique().tolist()
    for batch in chunked(values, 200):
        where = f"{match_col} IN ({','.join(quote_sql_value(value) for value in batch)})"
        try:
            delete_result = client.delete_features(conn, layer_url, where=where)
            outcome.deleted_count += int(delete_result.get("deleted_count", 0) or 0)
            if not delete_result.get("success"):
                outcome.errors.extend(delete_result.get("errors", []) or [])
        except Exception as exc:
            logger.error(f"deleteFeatures: {exc}", exc_info=True)
            outcome.errors.append(f"deleteFeatures: {exc}")


def _add_existing_layer_features(
    client: ArcGISPublishModeProviderProtocol,
    conn: ArcGISConnectionInfo,
    layer_url: str,
    gdf: object,
    mode: str,
    target_srid: int,
    outcome: ArcGISPublishModeLayerResult,
) -> None:
    try:
        features = client.dataframe_to_esri_features(
            gdf,
            "geometry",
            target_srid,
            operation="append",
        )
        add_result = client.add_features(conn, layer_url, features)
        outcome.added_count = int(add_result.get("added_count", 0) or 0)
        if add_result.get("errors"):
            outcome.errors.extend(add_result["errors"])
    except Exception as exc:
        logger.error(f"add_features ({mode}): {exc}", exc_info=True)
        outcome.errors.append(f"add_features: {exc}")


__all__ = [
    "ArcGISPublishModeLayerResult",
    "ArcGISPublishModeProviderProtocol",
    "publish_existing_layer_mode",
]
