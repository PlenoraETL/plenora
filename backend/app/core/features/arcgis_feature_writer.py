"""ArcGIS feature write orchestration boundary."""

from __future__ import annotations

from typing import List, Optional, Protocol, Tuple

import pandas as pd

from app.core.features.arcgis_writer_boundary import (
    ArcGISColumnMappingsInput,
    normalize_arcgis_column_mappings,
)
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import (
    ArcGISReadProviderProtocol,
    ArcGISServiceAdminProviderProtocol,
    ArcGISWriteProviderProtocol,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ArcGISFeatureWriteProviderProtocol(
    ArcGISReadProviderProtocol,
    ArcGISWriteProviderProtocol,
    ArcGISServiceAdminProviderProtocol,
    Protocol,
):
    """Provider capabilities required by feature write/publish operations."""


class ArcGISFeatureWriteService:
    """Coordinates ArcGIS feature mutation and service creation calls."""

    def __init__(self, client: ArcGISFeatureWriteProviderProtocol) -> None:
        self._client = client

    def delete_features(
        self,
        conn_info: ArcGISConnectionInfo,
        layer_url: str,
        object_ids: Optional[List[int]] = None,
        where: Optional[str] = None,
    ) -> dict:
        result = self._client.delete_features(
            conn_info,
            layer_url,
            object_ids=object_ids,
            where=where,
        )
        return {
            "success": result["success"],
            "message": f"Eliminati {result['deleted_count']} features",
            "count": result["deleted_count"],
            "errors": result.get("errors", []),
        }

    def create_service_from_df(
        self,
        conn_info: ArcGISConnectionInfo,
        service_name: str,
        layer_name: str,
        df: pd.DataFrame,
        geometry_column: str = "geometry",
        source_srid: Optional[int] = None,
        folder_id: str = "",
        column_mappings: ArcGISColumnMappingsInput = None,
    ) -> dict:
        if source_srid and hasattr(df, "set_crs") and (getattr(df, "crs", None) is None):
            df = df.set_crs(epsg=source_srid)
            logger.info(f"CRS impostato da source_srid: EPSG:{source_srid}")

        mapped = normalize_arcgis_column_mappings(column_mappings).apply(
            df,
            geometry_column=geometry_column,
        )
        return self._client.create_service(
            conn_info,
            service_name=service_name,
            layer_name=layer_name,
            df=mapped.df,
            geometry_column=mapped.geometry_column,
            folder_id=folder_id,
        )

    def write_features_df_core(
        self,
        conn_info: ArcGISConnectionInfo,
        layer_url: str,
        operation: str,
        df: pd.DataFrame,
        geometry_column: str,
        source_srid: Optional[int],
        batch_size: Optional[int],
        match_column: str,
        column_mappings: ArcGISColumnMappingsInput,
    ) -> dict:
        if source_srid and hasattr(df, "set_crs") and getattr(df, "crs", None) is None:
            df = df.set_crs(epsg=source_srid)
            logger.info(f"CRS impostato da source_srid: EPSG:{source_srid}")

        mapped = normalize_arcgis_column_mappings(column_mappings).apply(
            df,
            geometry_column=geometry_column,
            required_match_column=match_column if operation in ("update", "upsert", "delete") else "",
        )
        features = self._client.dataframe_to_esri_features(
            mapped.df,
            geometry_column=mapped.geometry_column,
            operation=operation,
        )

        if not features:
            return {
                "success": False,
                "message": "Nessuna feature da caricare",
                "count": 0,
                "errors": [],
            }

        if operation == "add":
            result = self._client.add_features(
                conn_info,
                layer_url,
                features,
                batch_size=batch_size,
            )
            return self.write_result("Aggiunte", result, "added_count", len(features))

        if operation == "update":
            if match_column:
                features = self.resolve_objectids_for_update(
                    conn_info,
                    layer_url,
                    features,
                    match_column,
                )
                if not features:
                    return {
                        "success": False,
                        "message": f"Nessuna feature matchata per colonna '{match_column}'",
                        "count": 0,
                        "errors": [],
                    }
            result = self._client.update_features(
                conn_info,
                layer_url,
                features,
                batch_size=batch_size,
            )
            return self.write_result("Aggiornate", result, "updated_count", len(features))

        if operation == "upsert":
            if not match_column:
                raise ValueError("Colonna match obbligatoria per upsert")
            to_update, to_add = self.split_upsert_features(
                conn_info,
                layer_url,
                features,
                match_column,
            )
            total_updated = 0
            total_added = 0
            all_errors: list[dict] = []

            if to_update:
                res_upd = self._client.update_features(
                    conn_info,
                    layer_url,
                    to_update,
                    batch_size=batch_size,
                )
                total_updated = res_upd.get("updated_count", 0)
                all_errors.extend(res_upd.get("errors", []))

            if to_add:
                res_add = self._client.add_features(
                    conn_info,
                    layer_url,
                    to_add,
                    batch_size=batch_size,
                )
                total_added = res_add.get("added_count", 0)
                all_errors.extend(res_add.get("errors", []))

            total = total_updated + total_added
            return {
                "success": total > 0,
                "message": (
                    f"Upsert: {total_updated} aggiornate, {total_added} "
                    f"inserite su {len(features)} totali"
                ),
                "count": total,
                "feature_count": total,
                "updated": total_updated,
                "added": total_added,
                "errors": all_errors,
            }

        if operation == "delete":
            if not match_column:
                raise ValueError("Colonna match obbligatoria per delete")
            oid_features = self.resolve_objectids_for_update(
                conn_info,
                layer_url,
                features,
                match_column,
            )
            if not oid_features:
                return {
                    "success": False,
                    "message": f"Nessuna feature trovata per colonna '{match_column}'",
                    "count": 0,
                    "errors": [],
                }
            object_ids = [feature["attributes"]["OBJECTID"] for feature in oid_features]
            result = self._client.delete_features(
                conn_info,
                layer_url,
                object_ids=object_ids,
            )
            return {
                "success": result["success"],
                "message": f"Eliminate {result['deleted_count']} features",
                "count": result["deleted_count"],
                "feature_count": result["deleted_count"],
                "errors": result.get("errors", []),
            }

        raise ValueError(f"Operazione non supportata: {operation}")

    @staticmethod
    def write_result(action: str, result: dict, count_key: str, total: int) -> dict:
        count = result.get(count_key, 0)
        return {
            "success": result["success"],
            "message": f"{action} {count} features su {total} totali",
            "count": count,
            "feature_count": count,
            "errors": result.get("errors", []),
        }

    def split_upsert_features(
        self,
        conn_info: ArcGISConnectionInfo,
        layer_url: str,
        features: List[dict],
        match_column: str,
    ) -> Tuple[List[dict], List[dict]]:
        match_values = []
        for feature in features:
            match_value = feature.get("attributes", {}).get(match_column)
            if match_value is not None:
                match_values.append(match_value)

        if not match_values:
            return [], features

        oid_map, query_errors = self._client.query_oid_by_key(
            conn_info,
            layer_url,
            match_column,
            match_values,
        )
        for error in query_errors:
            logger.error(f"Errore query OBJECTID per upsert: {error}")

        to_update = []
        to_add = []
        for feature in features:
            match_val = feature.get("attributes", {}).get(match_column)
            oid = oid_map.get(str(match_val))
            if oid is not None:
                feature["attributes"]["OBJECTID"] = oid
                to_update.append(feature)
            else:
                feature["attributes"].pop("OBJECTID", None)
                feature["attributes"].pop("objectid", None)
                to_add.append(feature)

        logger.info(f"Upsert split: {len(to_update)} da aggiornare, {len(to_add)} da inserire")
        return to_update, to_add

    def resolve_objectids_for_update(
        self,
        conn_info: ArcGISConnectionInfo,
        layer_url: str,
        features: List[dict],
        match_column: str,
    ) -> List[dict]:
        match_values = []
        for feature in features:
            match_value = feature.get("attributes", {}).get(match_column)
            if match_value is not None:
                match_values.append(match_value)

        if not match_values:
            logger.warning(f"Nessun valore trovato per colonna match '{match_column}'")
            return []

        oid_map, query_errors = self._client.query_oid_by_key(
            conn_info,
            layer_url,
            match_column,
            match_values,
        )
        for error in query_errors:
            logger.error(f"Errore query OBJECTID per match: {error}")

        logger.info(f"Risolti {len(oid_map)} OBJECTID su {len(match_values)} valori match")

        resolved = []
        skipped = 0
        for feature in features:
            match_val = feature.get("attributes", {}).get(match_column)
            oid = oid_map.get(str(match_val))
            if oid is not None:
                feature["attributes"]["OBJECTID"] = oid
                resolved.append(feature)
            else:
                skipped += 1

        if skipped:
            logger.warning(f"Scartate {skipped} features senza match OBJECTID")

        return resolved
