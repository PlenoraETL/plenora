"""ArcGISClient feature write and add-layer methods.

This mixin owns layer-level feature mutation endpoints plus the IMDF-compatible
``add_layer_to_service`` public method. ``ArcGISClient`` provides the HTTP,
authentication, schema, and conversion helpers used here.
"""

import json
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests

from app.core.features.arcgis_rest_write import (
    ArcGISApplyEditsRequest,
    ArcGISDeleteFeaturesRequest,
    ArcGISFeatureBatchWriteRequest,
    ArcGISFeatureEditOperation,
    parse_apply_edits_result,
    parse_delete_features_result,
    parse_feature_edit_result,
)
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AddLayerResult:
    """Typed result for ``ArcGISClient.add_layer_to_service``.

    The IMDF compatibility wrapper still converts this to the legacy dict
    shape ``{success, feature_count, errors}``.
    """

    success: bool
    feature_count: int
    errors: Tuple[str, ...] = ()

    def to_compat_dict(self) -> dict:
        """Return the pre-BE-IMDF-006B legacy dict shape."""
        return {
            "success": self.success,
            "feature_count": self.feature_count,
            "errors": list(self.errors),
        }


class ArcGISFeatureWriteMethodsMixin:
    """Layer-level feature writes for ``ArcGISClient``."""

    def add_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        features: List[dict],
        batch_size: int = None,
    ) -> dict:
        """Add ESRI JSON features to a layer."""
        return self._write_batch(
            conn,
            layer_url,
            ArcGISFeatureEditOperation.ADD,
            features,
            batch_size=batch_size,
        )

    def update_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        features: List[dict],
        batch_size: int = None,
    ) -> dict:
        """Update ESRI JSON features in a layer."""
        return self._write_batch(
            conn,
            layer_url,
            ArcGISFeatureEditOperation.UPDATE,
            features,
            batch_size=batch_size,
        )

    def delete_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        object_ids: List[int] = None,
        where: str = None,
    ) -> dict:
        """Delete features from a layer by ObjectID list or WHERE clause."""
        referer_headers = {"Referer": conn.server_url}
        request = ArcGISDeleteFeaturesRequest(
            layer_url=layer_url,
            object_ids=tuple(object_ids or ()),
            where=where,
        )
        data = request.body()

        try:
            response = self._request_with_token(
                "POST",
                request.endpoint(),
                conn,
                data=data,
                timeout=self.DEFAULT_TIMEOUT,
                extra_headers=referer_headers,
            )
            result = response.json()
            return parse_delete_features_result(result).to_compat_dict()

        except requests.RequestException as e:
            return {
                "success": False,
                "deleted_count": 0,
                "errors": [{"message": str(e)}],
            }

    def apply_edits(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        adds: Optional[List[dict]] = None,
        updates: Optional[List[dict]] = None,
        deletes: Optional[List[int]] = None,
        rollback_on_failure: bool = True,
        use_global_ids: bool = False,
    ) -> dict:
        """Apply mixed layer edits through the ArcGIS ``applyEdits`` endpoint."""
        referer_headers = {"Referer": conn.server_url}
        request = ArcGISApplyEditsRequest(
            layer_url=layer_url,
            adds=tuple(adds or ()),
            updates=tuple(updates or ()),
            deletes=tuple(deletes or ()),
            rollback_on_failure=rollback_on_failure,
            use_global_ids=use_global_ids,
        )

        try:
            response = self._request_with_token(
                "POST",
                request.endpoint(),
                conn,
                data=request.body(),
                timeout=60,
                extra_headers=referer_headers,
            )
            return parse_apply_edits_result(response.json()).to_compat_dict()
        except requests.RequestException as e:
            return {
                "success": False,
                "added_count": 0,
                "updated_count": 0,
                "deleted_count": 0,
                "errors": [{"message": str(e)}],
            }

    def _write_batch(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        operation: ArcGISFeatureEditOperation | str,
        features: List[dict],
        batch_size: int = None,
    ) -> dict:
        """Execute add/update writes in ArcGIS-compatible batches."""
        operation = ArcGISFeatureEditOperation(operation)
        referer_headers = {"Referer": conn.server_url}

        total_success = 0
        all_errors = []

        _batch_size = batch_size if batch_size and batch_size > 0 else self.WRITE_BATCH_SIZE
        for i in range(0, len(features), _batch_size):
            batch = features[i:i + _batch_size]
            request = ArcGISFeatureBatchWriteRequest(
                layer_url=layer_url,
                operation=operation,
                features=tuple(batch),
            )
            data = request.body()

            try:
                batch_num = i // _batch_size + 1
                logger.debug(
                    f"Batch {batch_num}: invio {len(batch)} features a "
                    f"{request.endpoint()}"
                )

                response = self._request_with_token(
                    "POST",
                    request.endpoint(),
                    conn,
                    data=data,
                    timeout=60,
                    extra_headers=referer_headers,
                )
                try:
                    result = response.json()
                except Exception as json_err:
                    logger.error(
                        f"[CRASH] Batch {batch_num}: risposta non JSON "
                        f"({response.status_code}): {response.text[:200]}"
                    )
                    all_errors.append({"message": f"JSON parse error: {json_err}"})
                    continue

                batch_result = parse_feature_edit_result(operation, result)
                successes = batch_result.count
                errors = list(batch_result.errors)

                logger.debug(f"Batch {batch_num}: {successes} ok, {len(errors)} errori")
                if errors:
                    logger.error(
                        f"[GESTITO] Batch {batch_num} errori (primi 2): {errors[:2]}"
                    )

                total_success += successes
                all_errors.extend(errors)

            except requests.RequestException as e:
                all_errors.append({"message": str(e)})

        count_key = operation.count_key
        total_attempted = len(features)
        has_errors = len(all_errors) > 0
        write_success = (total_success > 0 or total_attempted == 0) and not has_errors

        return {
            "success": write_success,
            count_key: total_success,
            "errors": all_errors,
        }

    def add_layer_to_service(
        self,
        conn: ArcGISConnectionInfo,
        service_url: str,
        gdf,
        layer_name: str,
        layer_id: int,
        srid: int = 4326,
    ) -> AddLayerResult:
        """Add one layer to an existing Feature Service."""
        import geopandas as gpd

        token = self.authenticate(conn)
        referer = {"Referer": conn.server_url}
        admin_url = service_url.replace("/rest/services/", "/rest/admin/services/")

        geom_type = "esriGeometryPolygon"
        if isinstance(gdf, gpd.GeoDataFrame) and len(gdf) > 0 and gdf.geometry is not None:
            geom_types = gdf.geometry.dropna().geom_type
            if len(geom_types) > 0:
                majority_type = geom_types.value_counts().idxmax()
                geom_type = self._esri_geometry_type(majority_type)
                unique_types = set(geom_types)
                if len(unique_types) > 1:
                    logger.warning(
                        f"add_layer_to_service('{layer_name}'): DataFrame ha "
                        f"{len(unique_types)} tipi di geometria ({unique_types}); "
                        f"usato '{majority_type}', le altre potrebbero fallire."
                    )
            if gdf.crs:
                srid = gdf.crs.to_epsg() or srid

        fields = [
            {
                "name": "OBJECTID",
                "type": "esriFieldTypeOID",
                "alias": "OBJECTID",
                "editable": False,
            },
        ]
        for col in gdf.columns:
            if col in ("geometry", "OBJECTID", "objectid"):
                continue
            fields.append(self._python_type_to_esri_field(col, gdf[col].dtype))

        extent = {
            "xmin": -180,
            "ymin": -90,
            "xmax": 180,
            "ymax": 90,
            "spatialReference": {"wkid": srid},
        }
        try:
            if isinstance(gdf, gpd.GeoDataFrame) and gdf.geometry is not None and len(gdf) > 0:
                bounds = gdf.geometry.dropna().total_bounds
                if len(bounds) == 4 and all(abs(b) < 1e15 for b in bounds):
                    extent = {
                        "xmin": float(bounds[0]),
                        "ymin": float(bounds[1]),
                        "xmax": float(bounds[2]),
                        "ymax": float(bounds[3]),
                        "spatialReference": {"wkid": srid},
                    }
        except Exception as e:
            logger.warning(f"Impossibile calcolare extent per {layer_name}: {e}")

        drawing_info = self._default_drawing_info(geom_type)

        layer_def = {
            "layers": [{
                "id": layer_id,
                "name": layer_name,
                "type": "Feature Layer",
                "geometryType": geom_type,
                "objectIdField": "OBJECTID",
                "fields": fields,
                "extent": extent,
                "drawingInfo": drawing_info,
                "hasAttachments": False,
                "capabilities": "Create,Delete,Query,Update,Editing",
            }]
        }

        add_def_data = {
            "addToDefinition": json.dumps(layer_def),
            "f": "json",
        }
        if token:
            add_def_data["token"] = token

        response = self._http.post(
            f"{admin_url}/addToDefinition",
            data=add_def_data,
            timeout=60,
            headers=referer,
        )
        add_result = response.json()
        if not add_result.get("success"):
            return AddLayerResult(
                success=False,
                feature_count=0,
                errors=(f"addToDefinition fallito: {add_result}",),
            )

        layer_url = f"{service_url}/{layer_id}"
        features = self.dataframe_to_esri_features(
            gdf,
            "geometry",
            srid,
            operation="create_service",
        )
        write_result = self.add_features(conn, layer_url, features)

        return AddLayerResult(
            success=write_result.get("added_count", 0) > 0,
            feature_count=write_result.get("added_count", 0),
            errors=tuple(write_result.get("errors", [])),
        )
