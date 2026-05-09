"""ArcGIS Feature Service read/query helpers."""

import json
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import requests

from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_sql import (
    chunk_arcgis_values,
    quote_arcgis_sql_value,
)
from app.shared.connections.esri_json import EsriJsonConverter
from app.shared.connections.esri_types import EsriTypeMapper
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _quote_arcgis_sql_value(value: object) -> str:
    """Quote a Python value for an ArcGIS SQL WHERE literal."""
    return quote_arcgis_sql_value(value)


def _chunked_values(values: Sequence[object], size: int) -> Iterator[Sequence[object]]:
    """Yield ordered chunks of at most ``size`` values."""
    yield from chunk_arcgis_values(values, size)


class ArcGISReadQueryMixin:
    """Layer metadata and feature query operations for ArcGIS clients."""

    def list_layers(
        self,
        conn: ArcGISConnectionInfo,
        service_url: str
    ) -> List[dict]:
        """
        Lista i layer di un Feature Service.

        Gestisce vari formati di URL ArcGIS Online:
        - URL diretti (services.arcgis.com/...)
        - URL proxy (utility.arcgis.com/usrsvcs/...)
        - MapServer -> FeatureServer fallback

        Returns:
            Lista di layer con {id, name, geometry_type, feature_count, url}
        """
        self.authenticate(conn)

        urls_to_try = self._build_layer_urls(service_url)

        logger.info(f"list_layers: URL candidati: {urls_to_try}")

        params = {"f": "json"}

        last_error = ""

        for url in urls_to_try:
            try:
                logger.info(f"list_layers: provo {url}")
                response = self._request_with_token(
                    "GET", url, conn, params=params, timeout=self.DEFAULT_TIMEOUT,
                )
                data = response.json()

                if "error" in data:
                    error_msg = data['error'].get('message', '')
                    logger.warning(f"list_layers fallito per {url}: {error_msg}")
                    last_error = error_msg
                    continue

                layers = []

                for layer in data.get("layers", []):
                    layer_info = {
                        "id": layer["id"],
                        "name": layer.get("name", f"Layer {layer['id']}"),
                        "geometry_type": layer.get("geometryType", ""),
                        "min_scale": layer.get("minScale", 0),
                        "max_scale": layer.get("maxScale", 0),
                        "url": f"{url}/{layer['id']}",
                    }
                    layer_info["feature_count"] = layer.get("count", None)
                    layers.append(layer_info)

                for table in data.get("tables", []):
                    layers.append({
                        "id": table["id"],
                        "name": table.get("name", f"Table {table['id']}"),
                        "geometry_type": "",
                        "url": f"{url}/{table['id']}",
                        "feature_count": None,
                    })

                logger.info(f"list_layers: {len(layers)} layer trovati per {url}")
                return layers

            except requests.RequestException as e:
                logger.warning(f"list_layers request fallita per {url}: {e}")
                last_error = str(e)
                continue

        raise ConnectionError(f"Errore servizio: {last_error}")

    def _build_layer_urls(self, service_url: str) -> List[str]:
        """
        Costruisce lista di URL candidati per il browse layer.

        Per URL ArcGIS Online, prova sia FeatureServer che MapServer
        perche' alcuni servizi proxy supportano solo MapServer.
        """
        urls = []
        url = service_url.rstrip("/")

        if url.endswith("/FeatureServer"):
            urls.append(url)
            urls.append(url.replace("/FeatureServer", "/MapServer"))
        elif url.endswith("/MapServer"):
            urls.append(url.replace("/MapServer", "/FeatureServer"))
            urls.append(url)
        else:
            urls.append(url + "/FeatureServer")
            urls.append(url + "/MapServer")

        return urls

    def get_layer_schema(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str
    ) -> dict:
        """
        Recupera lo schema completo di un layer (campi, tipi, capabilities).

        Equivalente a DatabaseInspector.get_tables_and_columns ma per un
        singolo layer. Restituisce campi con tipo PLENORA mappato.
        """
        params = {"f": "json"}

        try:
            response = self._request_with_token(
                "GET", layer_url, conn, params=params, timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()

            if "error" in data:
                raise ConnectionError(f"Errore layer: {data['error'].get('message', '')}")

            fields = []
            for f in data.get("fields", []):
                esri_type = f.get("type", "")
                fields.append({
                    "name": f.get("name", ""),
                    "alias": f.get("alias", f.get("name", "")),
                    "field_type": esri_type,
                    "urby_type": EsriTypeMapper.map_field_type(esri_type),
                    "length": f.get("length"),
                    "nullable": f.get("nullable", True),
                    "editable": f.get("editable", True),
                })

            feature_count = data.get("count", None)
            if feature_count is None:
                feature_count = self._get_feature_count(conn, layer_url, None)

            geometry_type = data.get("geometryType", "")

            spatial_ref = data.get("extent", {}).get("spatialReference",
                                     data.get("sourceSpatialReference", {}))
            wkid = spatial_ref.get("latestWkid") or spatial_ref.get("wkid")

            urby_geom = EsriTypeMapper.map_geometry_type(geometry_type)
            if wkid and urby_geom != "geometry":
                urby_geom = f"{urby_geom}:{wkid}"

            relationships = []
            base_service_url = layer_url.rsplit("/", 1)[0]
            for rel in data.get("relationships", []):
                cardinality = rel.get("cardinality", "")
                card_label = {
                    "esriRelCardinalityOneToOne": "1:1",
                    "esriRelCardinalityOneToMany": "1:N",
                    "esriRelCardinalityManyToMany": "N:N",
                }.get(cardinality, cardinality)

                role = rel.get("role", "")
                role_label = "origin" if "Origin" in role else "destination" if "Destination" in role else role

                related_table_id = rel.get("relatedTableId")
                related_url = f"{base_service_url}/{related_table_id}" if related_table_id is not None else ""

                relationships.append({
                    "id": rel.get("id"),
                    "name": rel.get("name", ""),
                    "related_table_id": related_table_id,
                    "related_url": related_url,
                    "cardinality": card_label,
                    "key_field": rel.get("keyField", ""),
                    "key_field_in_relationship_table": rel.get("keyFieldInRelationshipTable", ""),
                    "role": role_label,
                    "composite": rel.get("composite", False),
                })

            columns = []
            for f in fields:
                columns.append({
                    "name": f["name"],
                    "type": f["urby_type"],
                })
            if geometry_type:
                columns.append({
                    "name": "geometry",
                    "type": urby_geom,
                })

            return {
                "layer_id": data.get("id", 0),
                "name": data.get("name", ""),
                "geometry_type": geometry_type,
                "geometry_type_urby": urby_geom,
                "spatial_reference": spatial_ref,
                "fields": fields,
                "columns": columns,
                "feature_count": feature_count,
                "capabilities": data.get("capabilities", ""),
                "max_record_count": data.get("maxRecordCount", 1000),
                "description": data.get("description", ""),
                "relationships": relationships,
            }

        except requests.RequestException as e:
            raise ConnectionError(f"Errore connessione al layer: {e}")

    def _get_feature_count(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        token: str = None
    ) -> Optional[int]:
        """Query count delle features di un layer."""
        params = {
            "where": "1=1",
            "returnCountOnly": "true",
            "f": "json",
        }
        try:
            response = self._request_with_token(
                "GET", f"{layer_url}/query", conn,
                params=params, timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()
            return data.get("count")
        except Exception:
            return None

    def count_features_where(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        where: str,
        *,
        timeout: float = 60.0,
    ) -> int:
        """Conta le feature di un layer con WHERE arbitrario via POST."""
        token = self.authenticate(conn)
        referer = {"Referer": conn.server_url}
        data = {
            "where": where,
            "returnCountOnly": "true",
            "f": "json",
        }
        if token:
            data["token"] = token
        r = self._http.post(
            f"{layer_url}/query",
            data=data,
            headers=referer,
            timeout=timeout,
        )
        ct = (r.headers.get("content-type") or "").lower()
        if r.status_code != 200:
            raise RuntimeError(
                f"HTTP {r.status_code} su /query ({ct}): {r.text[:300]}"
            )
        if "json" not in ct:
            raise RuntimeError(
                f"Risposta non-JSON ({ct}) su /query: {r.text[:300]}"
            )
        try:
            payload = r.json()
        except Exception as je:
            raise RuntimeError(f"JSON decode fallito: {je}; body: {r.text[:300]}")
        if "error" in payload:
            raise RuntimeError(f"Errore ArcGIS: {payload['error']}")
        return int(payload.get("count", 0))

    def query_oid_by_key(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        match_col: str,
        values: Sequence[object],
    ) -> Tuple[Dict[str, int], List[str]]:
        """Resolve ``{business_key: OBJECTID}`` from a Feature Service layer."""
        token = self.authenticate(conn)
        referer_headers = {"Referer": conn.server_url}
        oid_by_key: Dict[str, int] = {}
        errors: List[str] = []
        batches_ok = 0
        batches_failed = 0

        for batch in _chunked_values(values, 200):
            where = (
                f"{match_col} IN "
                f"({','.join(_quote_arcgis_sql_value(value) for value in batch)})"
            )
            params = {
                "where": where,
                "outFields": f"OBJECTID,{match_col}",
                "returnGeometry": "false",
                "f": "json",
            }
            if token:
                params["token"] = token

            try:
                response = self._http.get(
                    f"{layer_url}/query",
                    params=params,
                    timeout=self.DEFAULT_TIMEOUT,
                    headers=referer_headers,
                )
                payload = response.json()
                for feature in payload.get("features") or []:
                    attributes = feature.get("attributes") or {}
                    key = attributes.get(match_col)
                    oid = attributes.get("OBJECTID") or attributes.get("objectid")
                    if key is not None and oid is not None:
                        oid_by_key[str(key)] = int(oid)
                batches_ok += 1
            except Exception as exc:
                batches_failed += 1
                batch_number = batches_ok + batches_failed
                errors.append(f"query OBJECTID batch {batch_number}: {exc}")

        if errors or len(oid_by_key) < len(values):
            logger.warning(
                "query_oid_by_key(%s): expected %s values, resolved %s OID. "
                "Batches: %s ok, %s failed. Errors: %s",
                match_col,
                len(values),
                len(oid_by_key),
                batches_ok,
                batches_failed,
                len(errors),
            )
        return oid_by_key, errors

    def query_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        where: str = "1=1",
        out_fields: str = "*",
        return_geometry: bool = True,
        geometry_filter: dict = None,
        result_offset: int = 0,
        result_record_count: int = None,
        order_by: str = None,
        out_sr: int = None,
        max_features: int = None,
    ) -> Tuple[List[tuple], List[str]]:
        """Query features da un layer e restituisce (righe, colonne)."""
        self.authenticate(conn)

        schema = self.get_layer_schema(conn, layer_url)
        max_record_count = schema.get("max_record_count", 1000)
        geometry_type = schema.get("geometry_type", "")
        fields_def = schema.get("fields", [])

        oid_field = self._find_oid_field(fields_def)

        all_features = []
        current_offset = result_offset
        page_size = result_record_count or max_record_count

        while True:
            params = {
                "where": where,
                "outFields": out_fields,
                "returnGeometry": "true" if return_geometry else "false",
                "resultOffset": current_offset,
                "resultRecordCount": page_size,
                "f": "json",
            }

            if geometry_filter:
                params["geometry"] = json.dumps(geometry_filter.get("geometry", {}))
                params["geometryType"] = geometry_filter.get("geometryType", "esriGeometryEnvelope")
                params["spatialRel"] = geometry_filter.get("spatialRel", "esriSpatialRelIntersects")
                params["inSR"] = geometry_filter.get("inSR", "")

            if order_by:
                params["orderByFields"] = order_by
            elif oid_field:
                params["orderByFields"] = f"{oid_field} ASC"

            if out_sr:
                params["outSR"] = out_sr

            try:
                response = self._request_with_token(
                    "GET", f"{layer_url}/query", conn,
                    params=params, timeout=self.DEFAULT_TIMEOUT,
                )
                data = response.json()

                if "error" in data:
                    error_msg = data["error"].get("message", "Errore query")
                    raise RuntimeError(f"Errore query Feature Service: {error_msg}")

                features = data.get("features", [])

                if not features:
                    break

                all_features.extend(features)

                if max_features and len(all_features) >= max_features:
                    all_features = all_features[:max_features]
                    break

                exceeded = data.get("exceededTransferLimit", False)
                if not exceeded or len(features) < page_size:
                    break

                current_offset += len(features)

            except requests.RequestException as e:
                raise ConnectionError(f"Errore query: {e}")

        if out_fields and out_fields != "*":
            requested_fields = set(f.strip() for f in out_fields.split(","))
            fields_def_filtered = [
                f for f in fields_def
                if f["name"] in requested_fields
            ]
        else:
            fields_def_filtered = fields_def

        return EsriJsonConverter.features_to_rows(
            all_features, fields_def_filtered,
            include_geometry=return_geometry,
            geometry_type=geometry_type
        )

    def _find_oid_field(self, fields: List[dict]) -> Optional[str]:
        """Trova il campo ObjectID nel layer."""
        for f in fields:
            if f.get("field_type") == "esriFieldTypeOID":
                return f["name"]
        for f in fields:
            if f["name"].upper() in ("OBJECTID", "OID", "FID"):
                return f["name"]
        return None
