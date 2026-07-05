"""
Client REST per ArcGIS Server / Enterprise / Online.

Keeps provider-specific behavior behind a narrow integration boundary.
"""

from urllib.parse import urlparse

import json
from typing import Dict, Iterator, Mapping, Optional, Sequence, Tuple

import requests

from app.core.dataframes import geometry_parsing as geometry_parsing_module
from app.core.dataframes import values as dataframe_values_module
from app.shared.connections.esri_system_fields import ESRI_OID_FIELDS_LITERAL
from app.shared.connections.arcgis_provider_results import (
    DeleteFeatureServiceItemsResult,
    ItemDeleteResult,
    RendererUpdateResult,
)
from app.core.features.arcgis_auth import (
    ArcGISAuthMixin,
    DefaultHttpClient as _DefaultHttpClient,
    HttpClientProtocol as _HttpClientProtocol,
)
from app.core.features.arcgis_service_discovery import ArcGISServiceDiscoveryMixin
from app.core.features.arcgis_read import ArcGISReadQueryMixin
from app.core.features.arcgis_feature_write_methods import (
    ArcGISFeatureWriteMethodsMixin,
)
from app.core.features.arcgis_json_types import (
    ArcGISJsonList,
    ArcGISJsonObject,
)
from app.core.features.arcgis_rest_write import (
    ArcGISAddToDefinitionRequest,
    ArcGISCreateServiceRequest,
    ArcGISUpdateDefinitionRequest,
)
from app.core.dataframes.geodata import (
    geodataframe_majority_geometry_type,
    geodataframe_total_bounds,
    is_geodataframe as _is_geodataframe,
)
from app.core.dataframes.tabular import non_null_head_values, object_series
from app.shared.connections.arcgis_connection import (
    ArcGISConnectionInfo as _ArcGISConnectionInfo,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = ["ArcGISClient"]


# CLIENT PRINCIPALE

class ArcGISClient(
    ArcGISFeatureWriteMethodsMixin,
    ArcGISReadQueryMixin,
    ArcGISServiceDiscoveryMixin,
    ArcGISAuthMixin,
):
    """
    Client REST per ArcGIS Feature Service.
    
    Dependency Injection:
    - http_client: sostituibile con mock per test (evita chiamate HTTP reali)
    
    Uso in produzione:
        client = ArcGISClient
        
    Uso nei test:
        mock_http = MockHttpClient
        client = ArcGISClient(http_client=mock_http)
    """
    
    # per operazioni di scrittura (limite Esri: max 2000
    WRITE_BATCH_SIZE = 1000
    
    # Timeout per richieste HTTP
    DEFAULT_TIMEOUT = 30
    
    def __init__(self, http_client: _HttpClientProtocol = None) -> None:
        self._http = http_client or _DefaultHttpClient()
        self._token_cache: Dict[str, dict] = {}  # url → {token, expires}

    # CONVERSIONE: DataFrame → Esri JSON
    
    @staticmethod
    def dataframe_to_esri_features(
        df,
        geometry_column: str = "geometry",
        srid: int = 4326,
        operation: str = "add",
    ) -> ArcGISJsonList:
        """
        Converte un DataFrame/GeoDataFrame in una lista di features Esri JSON.
        
        Supporta:
        - GeoDataFrame con colonna geometry (shapely)
        - DataFrame con colonna geometry (WKT string)
        
        Args:
            df: DataFrame o GeoDataFrame
            geometry_column: nome colonna geometria (default: geometry)
            srid: SRID della geometria (default: 4326)
            
        Returns:
            Lista di features in formato Esri JSON:
            [{"attributes": {...}, "geometry": {...}}, ...]
        """
        features = list(
            ArcGISClient.iter_dataframe_esri_features(
                df,
                geometry_column=geometry_column,
                srid=srid,
                operation=operation,
            )
        )
        logger.debug(f"Convertite {len(features)} features (srid={srid})")
        return features

    @staticmethod
    def iter_dataframe_esri_features(
        df,
        geometry_column: str = "geometry",
        srid: int = 4326,
        operation: str = "add",
    ) -> Iterator[ArcGISJsonObject]:
        """Yield ESRI JSON features without materializing the full feature list."""
        has_geometry = False
        is_geodataframe = False

        try:
            if _is_geodataframe(df) and df.geometry is not None:
                is_geodataframe = True
                has_geometry = True
                if df.crs:
                    srid = df.crs.to_epsg() or srid
        except ImportError:
            pass

        columns = list(df.columns)
        if not is_geodataframe and geometry_column in columns:
            has_geometry = True

        skip_cols = {geometry_column, "geometry"}
        if operation in ("add", "create_service"):
            skip_cols.update(ESRI_OID_FIELDS_LITERAL)
        attr_positions = [
            (col, pos)
            for pos, col in enumerate(columns)
            if col not in skip_cols
        ]

        geometry_pos = columns.index(geometry_column) if geometry_column in columns else None
        geometry_iter = iter(df.geometry) if is_geodataframe else None
        spatial_ref = {"wkid": srid}

        for row in df.itertuples(index=False, name=None):
            attributes = {}
            for col, pos in attr_positions:
                attribute_value = row[pos]
                if dataframe_values_module.is_null_scalar(attribute_value):
                    attributes[col] = None
                elif hasattr(attribute_value, "item"):
                    attributes[col] = attribute_value.item()
                else:
                    attributes[col] = attribute_value

            feature = {"attributes": attributes}

            if has_geometry:
                geom = None
                if is_geodataframe and geometry_iter is not None:
                    geom = next(geometry_iter)
                elif geometry_pos is not None:
                    raw_geom = row[geometry_pos]
                    if not dataframe_values_module.is_null_scalar(raw_geom):
                        geom = geometry_parsing_module.parse_geometry(raw_geom)

                if geom is not None and not geom.is_empty:
                    feature["geometry"] = ArcGISClient._shapely_to_esri(geom, spatial_ref)

            yield feature

    @staticmethod
    def iter_dataframe_esri_feature_batches(
        df,
        geometry_column: str = "geometry",
        srid: int = 4326,
        operation: str = "add",
        batch_size: int = 1000,
    ) -> Iterator[ArcGISJsonList]:
        """Yield ESRI JSON features in bounded batches."""
        size = batch_size if batch_size and batch_size > 0 else 1000
        batch: ArcGISJsonList = []
        for feature in ArcGISClient.iter_dataframe_esri_features(
            df,
            geometry_column=geometry_column,
            srid=srid,
            operation=operation,
        ):
            batch.append(feature)
            if len(batch) >= size:
                yield batch
                batch = []
        if batch:
            yield batch
    
    @staticmethod
    def _shapely_to_esri(geom, spatial_ref: dict) -> ArcGISJsonObject | None:
        """
        Converte una geometria Shapely in formato Esri JSON.
        
        Supporta Point, MultiPoint, LineString, MultiLineString,
        Polygon, MultiPolygon.
        """
        geom_type = geom.geom_type
        
        if geom_type == "Point":
            return {
                "x": geom.x,
                "y": geom.y,
                "spatialReference": spatial_ref,
            }
        
        elif geom_type == "MultiPoint":
            return {
                "points": [[p.x, p.y] for p in geom.geoms],
                "spatialReference": spatial_ref,
            }
        
        elif geom_type == "LineString":
            return {
                "paths": [list(geom.coords)],
                "spatialReference": spatial_ref,
            }
        
        elif geom_type == "MultiLineString":
            return {
                "paths": [list(line.coords) for line in geom.geoms],
                "spatialReference": spatial_ref,
            }
        
        elif geom_type == "Polygon":
            rings = [list(geom.exterior.coords)]
            for interior in geom.interiors:
                rings.append(list(interior.coords))
            return {
                "rings": rings,
                "spatialReference": spatial_ref,
            }
        
        elif geom_type == "MultiPolygon":
            rings = []
            for polygon in geom.geoms:
                rings.append(list(polygon.exterior.coords))
                for interior in polygon.interiors:
                    rings.append(list(interior.coords))
            return {
                "rings": rings,
                "spatialReference": spatial_ref,
            }
        
        else:
            logger.warning(f"Tipo geometria non supportato per Esri JSON: {geom_type}")
            return None
    
    @staticmethod
    def _esri_geometry_type(geom_type: str) -> str:
        """Converte tipo geometria shapely/urby → esriGeometryType."""
        mapping = {
            "Point": "esriGeometryPoint",
            "MultiPoint": "esriGeometryMultipoint",
            "LineString": "esriGeometryPolyline",
            "MultiLineString": "esriGeometryPolyline",
            "Polygon": "esriGeometryPolygon",
            "MultiPolygon": "esriGeometryPolygon",
            # Formati PLENORA
            "geometry:point": "esriGeometryPoint",
            "geometry:line": "esriGeometryPolyline",
            "geometry:polygon": "esriGeometryPolygon",
        }
        return mapping.get(geom_type, "esriGeometryPolygon")
    
    @staticmethod
    def _python_type_to_esri_field(col_name: str, dtype) -> ArcGISJsonObject:
        """Converte tipo colonna pandas → definizione campo Esri."""
        dtype_str = str(dtype)
        
        if "int" in dtype_str:
            return {"name": col_name, "type": "esriFieldTypeInteger", "alias": col_name}
        elif "float" in dtype_str:
            return {"name": col_name, "type": "esriFieldTypeDouble", "alias": col_name}
        elif "datetime" in dtype_str:
            return {"name": col_name, "type": "esriFieldTypeDate", "alias": col_name}
        else:
            return {"name": col_name, "type": "esriFieldTypeString", "alias": col_name, "length": 255}
    
    @staticmethod
    def _default_drawing_info(geom_type: str) -> ArcGISJsonObject:
        """Genera drawingInfo con stile di default visibile per il tipo di geometria."""
        # Colore fill: blu semitrasparente — outline: blu scuro
        fill_color = [51, 136, 255, 130]    # #3388FF ~50% alpha
        outline_color = [20, 72, 168, 255]  # #1448A8 opaco
        line_color = [51, 136, 255, 255]    # #3388FF opaco
        marker_color = [51, 136, 255, 255]

        if geom_type in ("esriGeometryPoint", "esriGeometryMultipoint"):
            return {
                "renderer": {
                    "type": "simple",
                    "symbol": {
                        "type": "esriSMS",
                        "style": "esriSMSCircle",
                        "color": marker_color,
                        "size": 8,
                        "outline": {
                            "type": "esriSLS",
                            "style": "esriSLSSolid",
                            "color": outline_color,
                            "width": 1,
                        },
                    },
                }
            }

        if geom_type == "esriGeometryPolyline":
            return {
                "renderer": {
                    "type": "simple",
                    "symbol": {
                        "type": "esriSLS",
                        "style": "esriSLSSolid",
                        "color": line_color,
                        "width": 2,
                    },
                }
            }

        # Polygon (default
        return {
            "renderer": {
                "type": "simple",
                "symbol": {
                    "type": "esriSFS",
                    "style": "esriSFSSolid",
                    "color": fill_color,
                    "outline": {
                        "type": "esriSLS",
                        "style": "esriSLSSolid",
                        "color": outline_color,
                        "width": 1,
                    },
                },
            }
        }

    def create_service(
        self,
        conn: _ArcGISConnectionInfo,
        service_name: str,
        layer_name: str,
        df,
        geometry_column: str = "geometry",
        srid: int = 4326,
        folder_id: str = "",
    ) -> ArcGISJsonObject:
        """
        Crea un nuovo Feature Service su ArcGIS Online con layer e schema
        derivati dal DataFrame.
        
        Flusso:
        1. createService → crea il servizio vuoto
        2. addToDefinition → aggiunge layer con schema
        3. addFeatures → carica i dati
        
        Args:
            conn: configurazione connessione
            service_name: nome del nuovo servizio
            layer_name: nome del layer
            df: DataFrame con i dati
            geometry_column: colonna geometria
            srid: SRID
            
        Returns:
            {success, service_url, layer_url, feature_count}
        """
        token = self.authenticate(conn)
        username = conn.username
        
        if not username:
            raise ValueError("Username richiesto per creare un servizio")
        
        parsed = urlparse(conn.server_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
        
        
        units = "esriDecimalDegrees" if srid == 4326 else "esriMeters"

        create_params = {
            "name": service_name,
            "hasStaticData": False,
            "maxRecordCount": 2000,
            "supportedQueryFormats": "JSON",
            "capabilities": "Create,Delete,Query,Update,Editing",
            "allowGeometryUpdates": True,
            "units": units,
            "spatialReference": {"wkid": srid},
            "initialExtent": {
                "xmin": -180, "ymin": -90, "xmax": 180, "ymax": 90,
                "spatialReference": {"wkid": srid},
            },
            "xssPreventionInfo": {"xssPreventionEnabled": True},
        }
        
        create_request = ArcGISCreateServiceRequest(
            base_url=base_url,
            username=username,
            folder_id=folder_id,
            create_parameters=create_params,
        )
        create_data = create_request.body()

        # Header Referer necessario per token client=referer
        referer_headers = {"Referer": conn.server_url}

        try:
            create_url = create_request.endpoint()
            logger.debug(f"createService URL: {create_url}")
            logger.debug(f"createService params: {json.dumps(create_params, indent=2)}")
            logger.debug(f"Token presente: {bool(token)}, lunghezza: {len(token) if token else 0}")
            logger.debug(f"Referer: {conn.server_url}")

            response = self._request_with_token(
                "POST", create_url, conn,
                data=create_data,
                timeout=60,
                extra_headers=referer_headers,
            )
            result = response.json()
            logger.debug(f"createService risposta completa: {json.dumps(result, indent=2, default=str)}")
            
            if not result.get("success"):
                error = result.get("error", {})
                error_msg = error.get("message", "") if isinstance(error, dict) else str(error)
                error_code = error.get("code", "") if isinstance(error, dict) else ""
                full_msg = f"Errore creazione servizio: code={error_code}, message={error_msg}, full_response={result}"
                logger.error(full_msg)
                raise ConnectionError(full_msg)
            
            service_url = result.get("serviceurl", "")
            service_item_id = result.get("itemId", "")
            
            logger.info(f"Servizio creato: {service_name} -> {service_url}")
            
        except requests.RequestException as e:
            raise ConnectionError(f"Errore creazione servizio: {e}")
        
        
        # Determina tipo geometria dal DataFrame
        # Usiamo il tipo MAGGIORITARIO (value_counts) invece della prima
        # feature: se il df ha una feature stray di tipo diverso (es. 1
        # Point in mezzo a migliaia di Polygon per errore di caricamento
        # usare la prima potrebbe creare il servizio col tipo sbagliato →
        # tutte le altre fallirebbero in addFeatures
        geom_type = "esriGeometryPolygon"  # default
        try:
            majority_type, unique_types, detected_srid = geodataframe_majority_geometry_type(df)
            if majority_type:
                geom_type = self._esri_geometry_type(majority_type)
                if len(unique_types) > 1:
                    logger.warning(
                        f"create_service: DataFrame contiene {len(unique_types)} "
                        f"tipi di geometria diversi ({unique_types}), usato il "
                        f"maggioritario '{majority_type}'. Le feature di tipo "
                        f"diverso potrebbero fallire in addFeatures."
                    )
            if detected_srid:
                srid = detected_srid or srid
        except IndexError:
            pass
        
        if not geom_type.startswith("esri") and geometry_column in df.columns:
            sample_values = non_null_head_values(df[geometry_column], 1)
            sample = sample_values[0] if sample_values else None
            if sample:
                geom = geometry_parsing_module.parse_geometry(sample)
                if geom is not None:
                    geom_type = self._esri_geometry_type(geom.geom_type)
                else:
                    logger.debug("Impossibile inferire geom_type: parse_geometry None su sample")
        
        fields = [
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID", "editable": False},
        ]
        for col in df.columns:
            if col in (geometry_column, "geometry", "OBJECTID", "objectid"):
                continue
            fields.append(self._python_type_to_esri_field(col, df[col].dtype))
        
        # Calcola extent dai dati
        extent = {
            "xmin": -180, "ymin": -90, "xmax": 180, "ymax": 90,
            "spatialReference": {"wkid": srid},
        }
        try:
            bounds = geodataframe_total_bounds(df)
            if bounds is not None:
                if len(bounds) == 4 and all(abs(b) < 1e15 for b in bounds):
                    extent = {
                        "xmin": float(bounds[0]),
                        "ymin": float(bounds[1]),
                        "xmax": float(bounds[2]),
                        "ymax": float(bounds[3]),
                        "spatialReference": {"wkid": srid},
                    }
                    logger.debug(f"Extent calcolato dai dati: {extent}")
            elif geometry_column in df.columns:
                geom_values = non_null_head_values(df[geometry_column], 1000)
                if geom_values:
                    geoms = [
                        geom for geom in geometry_parsing_module.parse_geometry_series(
                            object_series(geom_values)
                        ).dropna()
                        if geom is not None and not geom.is_empty
                    ]
                    if geoms:
                        from shapely.ops import unary_union as _union
                        combined = _union(geoms)
                        b = combined.bounds  # minx, miny, maxx, maxy
                        extent = {
                            "xmin": float(b[0]), "ymin": float(b[1]),
                            "xmax": float(b[2]), "ymax": float(b[3]),
                            "spatialReference": {"wkid": srid},
                        }
                        logger.debug(f"Extent calcolato da WKT: {extent}")
        except Exception as _e:
            logger.warning(f"Impossibile calcolare extent, uso default: {_e}")

        # Renderer di default basato sul tipo di geometria
        drawing_info = self._default_drawing_info(geom_type)

        layer_def = {
            "layers": [{
                "id": 0,
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
        
        # L'URL admin per aggiungere la definizione
        admin_url = service_url.replace("/rest/services/", "/rest/admin/services/")
        
        field_names = [f["name"] for f in fields]
        logger.debug(f"addToDefinition: {len(fields)} campi: {field_names}")
        logger.debug(f"addToDefinition URL: {admin_url}/addToDefinition")
        
        add_def_request = ArcGISAddToDefinitionRequest(
            admin_url=admin_url,
            definition=layer_def,
        )
        add_def_data = add_def_request.body()

        try:
            response = self._request_with_token(
                "POST", add_def_request.endpoint(), conn,
                data=add_def_data,
                timeout=60,
                extra_headers=referer_headers,
            )
            result = response.json()
            logger.debug(f"addToDefinition risposta: {json.dumps(result, indent=2, default=str)[:500]}")

            if not result.get("success"):
                logger.error(f"[GESTITO] Errore addToDefinition: {result}")
                # Non blocchiamo — il servizio è creato
            else:
                logger.debug(f"Layer definizione aggiunta: {layer_name}")
                
        except requests.RequestException as e:
            logger.error(f"[GESTITO] Errore addToDefinition: {e}")
        
        
        layer_url = f"{service_url}/0"
        sample_features = self.dataframe_to_esri_features(
            df.head(1),
            geometry_column,
            srid,
            operation="create_service",
        )
        
        # Log sample feature per debug
        if sample_features:
            sample = sample_features[0]
            sample_geom = sample.get("geometry", {})
            sample_attrs = list(sample.get("attributes", {}).keys())[:5]
            logger.debug(f"Sample feature — geometry keys: {list(sample_geom.keys()) if sample_geom else 'NONE'}")
            logger.debug(f"Sample feature — geometry snippet: {json.dumps(sample_geom, default=str)[:300]}")
            logger.debug(f"Sample feature — attribute keys (primi 5): {sample_attrs}")
        
        write_result = self.add_dataframe_features(
            conn,
            layer_url,
            df,
            geometry_column=geometry_column,
            srid=srid,
            operation="create_service",
        )
        
        logger.debug(f"addFeatures result: added={write_result.get('added_count', 0)}, "
                     f"errors={len(write_result.get('errors', []))}")
        if write_result.get("errors"):
            logger.error(f"[GESTITO] addFeatures errori (primi 3): {write_result['errors'][:3]}")
        
        try:
            update_def = {
                "layers": [{
                    "id": 0,
                    "extent": extent,
                }]
            }
            update_request = ArcGISUpdateDefinitionRequest(
                admin_url=admin_url,
                definition=update_def,
            )
            response = self._request_with_token(
                "POST", update_request.endpoint(), conn,
                data=update_request.body(),
                timeout=60,
                extra_headers=referer_headers,
            )
            upd_result = response.json()
            if upd_result.get("success"):
                logger.debug("Extent aggiornato sul servizio")
            else:
                logger.warning(f"updateDefinition extent non riuscito: {upd_result}")
        except Exception as _e:
            logger.warning(f"Impossibile aggiornare extent: {_e}")

        return {
            "success": write_result.get("added_count", 0) > 0,
            "service_url": service_url,
            "layer_url": layer_url,
            "item_id": service_item_id,
            "feature_count": write_result.get("added_count", 0),
            "errors": write_result.get("errors", []),
        }

    def test_connection(self, conn: _ArcGISConnectionInfo) -> ArcGISJsonObject:
        """
        Testa la connessione ArcGIS.
        
        Verifica:
        1. Raggiungibilità del server
        2. Autenticazione (se richiesta)
        3. Accesso al catalogo servizi
        
        Returns:
            {success, message, details}
        """
        try:
            if conn.auth_type != "public":
                token = self.authenticate(conn)
                if not token:
                    return {
                        "success": False,
                        "message": "Autenticazione fallita: nessun token ricevuto",
                    }
            
            services = self.list_services(conn)
            
            return {
                "success": True,
                "message": f"Connessione riuscita. {len(services)} Feature Service trovati.",
                "details": {
                    "server_url": conn.server_url,
                    "server_type": conn.server_type,
                    "feature_services_count": len(services),
                    "services": [s["name"] for s in services[:10]],  # max 10 per preview
                }
            }
            
        except ConnectionError as e:
            return {
                "success": False,
                "message": f"Errore connessione: {str(e)}",
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Errore: {str(e)}",
            }


    def _renderer_update_result(
        self,
        conn: _ArcGISConnectionInfo,
        layer_url: str,
        drawing_info: dict,
    ) -> RendererUpdateResult:
        """
        Aggiorna il renderer (stile) di un layer tramite updateDefinition.

        Args:
            conn: configurazione connessione
            layer_url: URL del layer (es. .../FeatureServer/0)
            drawing_info: dict con la definizione drawingInfo Esri

        Returns:
            {success, message}
        """
        self.authenticate(conn)
        referer_headers = {"Referer": conn.server_url}

        admin_url = layer_url.replace("/rest/services/", "/rest/admin/services/")

        parts = layer_url.rstrip("/").split("/")
        layer_id = int(parts[-1]) if parts[-1].isdigit() else 0

        update_def = {
            "layers": [{
                "id": layer_id,
                "drawingInfo": drawing_info,
            }]
        }

        service_admin_url = "/".join(admin_url.split("/")[:-1]) if parts[-1].isdigit() else admin_url

        data = {
            "updateDefinition": json.dumps(update_def),
            "f": "json",
        }

        try:
            response = self._request_with_token(
                "POST", f"{service_admin_url}/updateDefinition", conn,
                data=data,
                timeout=60,
                extra_headers=referer_headers,
            )
            result = response.json()

            if result.get("success"):
                logger.info(f"Renderer aggiornato per layer {layer_id}")
                return RendererUpdateResult(success=True, message="Renderer aggiornato")
            else:
                error = result.get("error", {})
                msg = error.get("message", str(result))
                logger.error(f"[GESTITO] updateDefinition renderer fallito: {msg}")
                return RendererUpdateResult(success=False, message=msg)

        except requests.RequestException as e:
            return RendererUpdateResult(success=False, message=str(e))

    def update_renderer(
        self,
        conn: _ArcGISConnectionInfo,
        layer_url: str,
        drawing_info: dict,
    ) -> ArcGISJsonObject:
        """Apply renderer/drawingInfo and return the compatibility dict shape."""
        return self._renderer_update_result(conn, layer_url, drawing_info).to_compat_dict()

    def _layer_renderer_update_result(
        self,
        conn: _ArcGISConnectionInfo,
        layer_url: str,
        drawing_info: Mapping[str, object],
    ) -> RendererUpdateResult:
        """
        Aggiorna il renderer di un layer tramite layer-level updateDefinition.

        This is the generic provider boundary for ArcGIS endpoints that ignore
        service-level renderer updates while still returning success=True.
        Existing ``update_renderer`` remains service-level for compatibility.
        """
        layer_admin_url = layer_url.replace("/rest/services/", "/rest/admin/services/")
        update_def = {"drawingInfo": dict(drawing_info)}
        data = {
            "updateDefinition": json.dumps(update_def),
            "f": "json",
        }
        referer_headers = {"Referer": conn.server_url}

        try:
            response = self._request_with_token(
                "POST",
                f"{layer_admin_url.rstrip('/')}/updateDefinition",
                conn,
                data=data,
                timeout=60,
                extra_headers=referer_headers,
            )
            result = response.json()

            if result.get("success"):
                logger.info(f"Renderer aggiornato per layer URL {layer_url}")
                return RendererUpdateResult(success=True, message="Renderer aggiornato")

            error = result.get("error", {})
            msg = error.get("message", str(result)) if isinstance(error, dict) else str(result)
            logger.error(f"[GESTITO] layer updateDefinition renderer fallito: {msg}")
            return RendererUpdateResult(success=False, message=msg)

        except requests.RequestException as e:
            return RendererUpdateResult(success=False, message=str(e))

    def update_layer_renderer(
        self,
        conn: _ArcGISConnectionInfo,
        layer_url: str,
        drawing_info: Mapping[str, object],
    ) -> ArcGISJsonObject:
        """
        Aggiorna il renderer di un layer tramite layer-level updateDefinition.

        Public provider compatibility returns the ``{success, message}``
        dict while the internal boundary uses ``RendererUpdateResult``.
        """
        return self._layer_renderer_update_result(
            conn,
            layer_url,
            drawing_info,
        ).to_compat_dict()

    def _portal_sharing_base_url(self, conn: _ArcGISConnectionInfo) -> str:
        """Return the portal sharing REST base for an ArcGIS connection."""

        parsed = urlparse(conn.server_url)
        return f"{parsed.scheme}://{parsed.netloc}/sharing/rest"

    def _delete_item_permanently_result(
        self,
        conn: _ArcGISConnectionInfo,
        item_id: str,
    ) -> ItemDeleteResult:
        """Delete a portal item permanently.

        ArcGIS Online supports ``permanentDelete=true`` on item delete. Older
        Enterprise installs may require a normal delete followed by the
        ``/permanentDelete`` endpoint, so this method preserves that fallback.
        """
        base_url = self._portal_sharing_base_url(conn)
        referer_headers = {"Referer": conn.server_url}
        delete_url = f"{base_url}/content/users/{conn.username}/items/{item_id}/delete"
        delete_data = {"f": "json", "permanentDelete": "true"}

        try:
            response = self._request_with_token(
                "POST",
                delete_url,
                conn,
                data=delete_data,
                timeout=self.DEFAULT_TIMEOUT,
                extra_headers=referer_headers,
            )
            result = response.json()
            if result.get("success"):
                return ItemDeleteResult(
                    success=True,
                    message="item deleted",
                    item_id=item_id,
                )

            logger.warning(f"delete item {item_id} risposta: {result}")

            fallback_response = self._request_with_token(
                "POST",
                delete_url,
                conn,
                data={"f": "json"},
                timeout=self.DEFAULT_TIMEOUT,
                extra_headers=referer_headers,
            )
            fallback_result = fallback_response.json()
            if not fallback_result.get("success"):
                return ItemDeleteResult(
                    success=False,
                    message=f"delete item failed: {result}",
                    item_id=item_id,
                    response=result,
                )

            permanent_url = (
                f"{base_url}/content/users/{conn.username}/items/{item_id}/permanentDelete"
            )
            permanent_response = self._request_with_token(
                "POST",
                permanent_url,
                conn,
                data={"f": "json"},
                timeout=self.DEFAULT_TIMEOUT,
                extra_headers=referer_headers,
            )
            permanent_result = permanent_response.json()
            if permanent_result.get("success"):
                return ItemDeleteResult(
                    success=True,
                    message="item permanent-deleted via fallback endpoint",
                    item_id=item_id,
                )
            return ItemDeleteResult(
                success=False,
                message=f"permanentDelete failed: {permanent_result}",
                item_id=item_id,
                response=permanent_result,
            )
        except Exception as e:
            logger.warning(f"delete item {item_id} fallita: {e}")
            return ItemDeleteResult(success=False, message=str(e), item_id=item_id)

    def delete_item_permanently(
        self,
        conn: _ArcGISConnectionInfo,
        item_id: str,
    ) -> ArcGISJsonObject:
        """Delete a portal item permanently and return the compatibility dict shape."""
        return self._delete_item_permanently_result(conn, item_id).to_compat_dict()

    def _delete_feature_service_items_result(
        self,
        conn: _ArcGISConnectionInfo,
        items: Sequence[Mapping[str, object]],
        service_name: str = "",
    ) -> DeleteFeatureServiceItemsResult:
        """Delete discovered Feature Service portal items.

        Discovery and selection policy stays with the caller; this method owns
        the generic ArcGIS portal item deletion mechanics and counters.
        """
        attempted = 0
        succeeded = 0

        for item in items:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            attempted += 1
            result = ItemDeleteResult.from_compat_dict(
                self.delete_item_permanently(conn, item_id),
                item_id,
            )
            if result.success:
                succeeded += 1
                logger.info(
                    f"Item ArcGIS cancellato (permanent): {item_id} "
                    f"('{service_name}', folder={item.get('ownerFolder') or 'root'})"
                )
            else:
                logger.warning(f"delete item {item_id} risposta: {result.to_compat_dict()}")

        return DeleteFeatureServiceItemsResult(attempted=attempted, succeeded=succeeded)

    def delete_feature_service_items(
        self,
        conn: _ArcGISConnectionInfo,
        items: Sequence[Mapping[str, object]],
        service_name: str = "",
    ) -> Tuple[int, int]:
        """Delete discovered Feature Service portal items and return counters."""
        return self._delete_feature_service_items_result(
            conn,
            items,
            service_name,
        ).to_compat_tuple()

    def _resolve_hosting_admin_url(
        self,
        conn: _ArcGISConnectionInfo,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Resolve hosted server admin URL from portal metadata."""
        base_url = self._portal_sharing_base_url(conn)
        referer_headers = {"Referer": conn.server_url}

        try:
            response = self._request_with_token(
                "GET",
                f"{base_url}/portals/self",
                conn,
                params={"f": "json"},
                timeout=self.DEFAULT_TIMEOUT,
                extra_headers=referer_headers,
            )
            portal_info = response.json()
        except Exception as e:
            return None, f"portal/self failed: {e}"

        for server in (portal_info.get("helperServices") or {}).get("hostingServers", []) or []:
            admin_url = server.get("admin")
            if admin_url:
                return str(admin_url), None

        org_id = portal_info.get("id") or portal_info.get("orgId")
        if not org_id:
            return None, "hosting server URL non determinato da /portals/self"

        for host in ("services-eu1.arcgis.com", "services.arcgis.com", "services1.arcgis.com"):
            candidate = f"https://{host}/{org_id}/arcgis"
            try:
                response = self._request_with_token(
                    "GET",
                    f"{candidate}/admin/services",
                    conn,
                    params={"f": "json"},
                    timeout=15,
                    extra_headers=referer_headers,
                )
                if "services" in response.json():
                    return candidate, None
            except Exception:
                continue

        return None, "hosting server URL non determinato da /portals/self"

    def cleanup_zombie_feature_service(
        self,
        conn: _ArcGISConnectionInfo,
        service_name: str,
    ) -> Tuple[bool, str]:
        """Delete a hosted Feature Service whose portal item is not enumerable."""
        hosting_url, error = self._resolve_hosting_admin_url(conn)
        if not hosting_url:
            return False, error or "hosting server URL non determinato da /portals/self"

        referer_headers = {"Referer": conn.server_url}
        admin_base = f"{hosting_url.rstrip('/')}/admin/services"
        describe_url = f"{admin_base}/{service_name}.FeatureServer"

        try:
            response = self._request_with_token(
                "GET",
                describe_url,
                conn,
                params={"f": "json"},
                timeout=self.DEFAULT_TIMEOUT,
                extra_headers=referer_headers,
            )
            admin_info = response.json()
            if "error" in admin_info:
                return False, f"admin describe error: {admin_info['error']}"
            service_item_id = str(admin_info.get("serviceItemId") or "")
            if not service_item_id:
                return False, "serviceItemId non presente in admin describe"
        except Exception as e:
            return False, f"admin describe failed: {e}"

        delete_result = self.delete_item_permanently(conn, service_item_id)
        if delete_result.get("success"):
            logger.info(
                f"Zombie cleanup: item {service_item_id} ('{service_name}') "
                f"cancellato via admin fallback"
            )
            return True, f"item {service_item_id} cancellato"
        return False, str(delete_result.get("message") or delete_result)
