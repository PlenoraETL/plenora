"""
Servizio CRUD per connessioni ArcGIS Feature Service.

Keeps provider-specific behavior behind a narrow integration boundary.
"""

import time
from collections.abc import Mapping
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import settings
from app.utils.logger import get_logger
from app.core.results.pipeline_result import save_pipeline_result_outcome
from app.core.dataframes.frame_io import (
    dataframe_from_rows,
    load_arcgis_feature_dataframe,
)
from app.core.dataframes.ports import FrameLike
from app.core.features import arcgis_connection_store
from app.core.features.models import (
    ArcGISConnection,
    ArcGISCreate,
    ArcGISUpdate,
    ArcGISList,
    ArcGISTestResult,
)
from app.core.connections.arcgis_provider_factory import create_arcgis_provider
from app.core.features.arcgis_feature_operations import ArcGISFeatureOperations
from app.core.features.arcgis_feature_writer import ArcGISFeatureWriteService
from app.core.features.arcgis_writer_boundary import (
    ArcGISColumnMappingsInput,
    ArcGISFeatureWritePublicResult,
)
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISProviderProtocol

logger = get_logger(__name__)

# CRUD STORAGE — tabella arcgis_connections su PostgreSQL
# Merged da features_service_db.py (S4 consolidation). Le 5 funzioni
# pubbliche *_db e i 3 helper privati sono mantenuti come module-level
# per preservare la superficie importata dai test

DEFAULT_GROUP = "default"

ArcGISServiceInfo = Mapping[str, object]
ArcGISFolderInfo = Mapping[str, object]
ArcGISLayerInfo = Mapping[str, object]
ArcGISLayerSchema = Mapping[str, object]
ArcGISQueryExecutionResult = Mapping[str, object]


def _ensure_default_group() -> None:
    arcgis_connection_store.ensure_default_group()


def _row_to_connection(row: dict) -> ArcGISConnection:
    return arcgis_connection_store.row_to_connection(row)


def _generate_id(name: str) -> str:
    return arcgis_connection_store.generate_id(name)


def create_connection_db(data: ArcGISCreate, group_id: str = DEFAULT_GROUP,
                          created_by: Optional[str] = None) -> ArcGISConnection:
    return arcgis_connection_store.create_connection(data, group_id=group_id, created_by=created_by)


def list_connections_db(group_id: Optional[str] = None) -> ArcGISList:
    """
    Lista connessioni ArcGIS.
    - group_id None -> tutte (uso superadmin)
    - group_id "abc" -> solo del gruppo
    """
    return arcgis_connection_store.list_connections(group_id=group_id)


def get_connection_db(connection_id: str) -> Optional[ArcGISConnection]:
    """
    Ritorna la connessione (senza filtro gruppo).
    Il group check va fatto dal chiamante con `assert_group_access`.
    """
    return arcgis_connection_store.get_connection(connection_id)


def update_connection_db(connection_id: str, data: ArcGISUpdate) -> Optional[ArcGISConnection]:
    return arcgis_connection_store.update_connection(connection_id, data)


def delete_connection_db(connection_id: str) -> bool:
    return arcgis_connection_store.delete_connection(connection_id)


# SERVIZIO PRINCIPALE

class FeaturesService:
    """
    Servizio per gestione connessioni e operazioni su ArcGIS Feature Service.
    
    Combina il ruolo di ConnectionService (CRUD connessioni) con quello di
    DatabaseInspector (browse schema) e QueryExecutor (esecuzione query),
    adattati per il protocollo REST di ArcGIS.
    
    Dependency Injection:
    - client: ArcGISProviderProtocol (mockabile per test)
    - arcgis_dir: directory storage connessioni
    - results_dir: directory output risultati
    """

    def __init__(
        self,
        client: ArcGISProviderProtocol = None,
        arcgis_dir: Path = None,
        results_dir: Path = None,
    ) -> None:
        self._client = client or create_arcgis_provider()
        self._operations = ArcGISFeatureOperations(self._client)
        self._writer = ArcGISFeatureWriteService(self._client)
        self._arcgis_dir = arcgis_dir or settings.ARCGIS_CONNECTIONS_DIR
        self._results_dir = results_dir or Path(settings.RESULTS_DIR)
        
        # Crea directory se non esiste
    
    # CRUD CONNESSIONI
    
    def _generate_connection_id(self, name: str) -> str:
        """Genera ID univoco per connessione (stesso pattern di ConnectionService)."""
        return arcgis_connection_store.generate_id(name)
    
    def create_connection(self, data: ArcGISCreate, group_id: str = "default",
                          created_by: Optional[str] = None) -> ArcGISConnection:
        """Crea nuova connessione ArcGIS nel DB (assegnata al group_id dato)."""
        return create_connection_db(data, group_id=group_id, created_by=created_by)

    def list_connections(self, group_id: Optional[str] = None) -> ArcGISList:
        """Lista connessioni ArcGIS. group_id None = tutte (superadmin)."""
        return list_connections_db(group_id=group_id)
    
    def get_connection(self, connection_id: str) -> Optional[ArcGISConnection]:
        """Ottieni connessione per ID dal DB."""
        return get_connection_db(connection_id)
    
    def update_connection(self, connection_id: str, data: ArcGISUpdate) -> Optional[ArcGISConnection]:
        """Aggiorna connessione esistente nel DB."""
        return update_connection_db(connection_id, data)
    
    def delete_connection(self, connection_id: str) -> bool:
        """Elimina connessione dal DB."""
        return delete_connection_db(connection_id)
    
    def test_connection(self, connection_id: str) -> ArcGISTestResult:
        """
        Testa una connessione ArcGIS.
        
        Verifica raggiungibilità, autenticazione, e accesso al catalogo.
        """
        connection = self.get_connection(connection_id)
        if not connection:
            return ArcGISTestResult(
                success=False,
                message=f"Connessione '{connection_id}' non trovata"
            )
        
        conn_info = self._to_connection_info(connection)
        result = self._operations.test_connection(conn_info)
        
        return ArcGISTestResult(
            success=result["success"],
            message=result["message"],
            details=result.get("details"),
        )
    
    # BROWSE: SERVIZI E LAYER
    
    def browse_services(self, connection_id: str) -> list[ArcGISServiceInfo]:
        """
        Lista i Feature Service disponibili nella connessione.
        
        Equivalente a "lista tabelle" per un database.
        Per ArcGIS Online: usa Content API per cercare servizi dell'utente.
        Per ArcGIS Server: naviga il catalogo REST.
        
        Args:
            connection_id: ID della connessione ArcGIS
            
        Returns:
            Lista di servizi {name, url, folder}
        """
        connection = self.get_connection(connection_id)
        if not connection:
            raise ValueError(f"Connessione '{connection_id}' non trovata")
        
        conn_info = self._to_connection_info(connection)
        return self._operations.list_services(conn_info)
    
    def list_folders(self, connection_id: str) -> list[ArcGISFolderInfo]:
        """
        Lista le cartelle dell'utente nella connessione ArcGIS.

        Returns:
            Lista di {id, title}
        """
        connection = self.get_connection(connection_id)
        if not connection:
            raise ValueError(f"Connessione '{connection_id}' non trovata")
        conn_info = self._to_connection_info(connection)
        return self._operations.list_folders(conn_info)

    def search_services(
        self,
        connection_id: str,
        query: str = "",
        scope: str = "user",
    ) -> list[ArcGISServiceInfo]:
        """
        Cerca Feature Service su ArcGIS Online.
        
        Solo per connessioni ArcGIS Online. Per Server/Enterprise
        restituisce lo stesso di browse_services.
        
        Args:
            connection_id: ID della connessione
            query: termini di ricerca
            scope: "user" (miei), "org" (organizzazione), "all" (pubblici)
            
        Returns:
            Lista di servizi trovati
        """
        connection = self.get_connection(connection_id)
        if not connection:
            raise ValueError(f"Connessione '{connection_id}' non trovata")
        
        conn_info = self._to_connection_info(connection)
        
        return self._operations.search_services(conn_info, query=query, scope=scope)
    
    def browse_layers(self, connection_id: str, service_url: str) -> list[ArcGISLayerInfo]:
        """
        Lista i layer di un Feature Service.
        
        Equivalente a "lista colonne" per una tabella.
        
        Args:
            connection_id: ID della connessione ArcGIS
            service_url: URL del FeatureServer
            
        Returns:
            Lista di layer {id, name, geometry_type, feature_count, url}
        """
        connection = self.get_connection(connection_id)
        if not connection:
            raise ValueError(f"Connessione '{connection_id}' non trovata")
        
        conn_info = self._to_connection_info(connection)
        return self._operations.list_layers(conn_info, service_url)
    
    def get_layer_schema(self, connection_id: str, layer_url: str) -> ArcGISLayerSchema:
        """
        Recupera lo schema di un layer (campi + metadata).
        
        Equivalente a DatabaseInspector.get_tables_and_columns per un
        singolo layer.
        
        Args:
            connection_id: ID della connessione ArcGIS
            layer_url: URL del layer (es. .../FeatureServer/0)
            
        Returns:
            Schema con campi in formato PLENORA
        """
        connection = self.get_connection(connection_id)
        if not connection:
            raise ValueError(f"Connessione '{connection_id}' non trovata")
        
        conn_info = self._to_connection_info(connection)
        return self._operations.get_layer_schema(conn_info, layer_url)
    
    # QUERY (lettura dati
    
    def query_layer(
        self,
        connection_id: str,
        layer_url: str,
        where: str = "1=1",
        out_fields: str = "*",
        return_geometry: bool = True,
        max_features: int = None,
        out_sr: int = None,
    ) -> Tuple[List[tuple], List[str]]:
        """
        Query features da un layer — formato (righe, colonne).
        
        Interfaccia identica a QueryExecutor.execute_raw per
        compatibilità con il sistema ESTRAI.
        
        Args:
            connection_id: ID della connessione ArcGIS
            layer_url: URL del layer
            where: clausola WHERE
            out_fields: campi (* = tutti)
            return_geometry: includi geometria
            max_features: limite massimo features
            out_sr: SRID di output
            
        Returns:
            (righe, colonne)
        """
        connection = self.get_connection(connection_id)
        if not connection:
            raise ValueError(f"Connessione '{connection_id}' non trovata")
        
        conn_info = self._to_connection_info(connection)
        
        return self._operations.query_features(
            conn_info,
            layer_url,
            where=where,
            out_fields=out_fields,
            return_geometry=return_geometry,
            max_features=max_features,
            out_sr=out_sr,
        )
    
    def execute_query(
        self,
        connection_id: str,
        layer_url: str,
        project_id: str,
        output_filename: str,
        output_format: str = "csv",
        where: str = "1=1",
        out_fields: str = "*",
        return_geometry: bool = True,
        max_features: int = None,
        out_sr: int = None,
        geom_column: str = "geometry",
    ) -> ArcGISQueryExecutionResult:
        """
        Query completa con salvataggio file.
        
        Interfaccia identica a QueryExecutor.execute_query per
        compatibilità con il pipeline ESTRAI.
        
        Args:
            connection_id: ID della connessione ArcGIS
            layer_url: URL del layer
            project_id: ID progetto ESTRAI
            output_filename: nome file output
            output_format: formato (csv, json, xlsx, geojson, gpkg, shp)
            where: clausola WHERE
            out_fields: campi
            return_geometry: includi geometria
            max_features: limite features
            out_sr: SRID output
            geom_column: nome colonna geometria
            
        Returns:
            {success, message, rows_count, columns, output_path, timings}
        """
        timings = {}
        inizio = time.time()
        
        try:
            # Tombstone (QUERY-1 step 2): orphan `project_dir.mkdir(...)`
            # rimosso. `project_dir` non era mai usato (il salvataggio
            # passa da `save_pipeline_result_outcome` che gestisce
            # storage policy DB/S3 trasparentemente).
            #
            # Fase 0: Recupera CRS dal layer (per salvataggio geo corretto
            layer_crs = self._get_layer_crs(connection_id, layer_url, out_sr)
            
            # Fase 1: Query
            t_query = time.time()
            rows, columns = self.query_layer(
                connection_id, layer_url,
                where=where,
                out_fields=out_fields,
                return_geometry=return_geometry,
                max_features=max_features,
                out_sr=out_sr,
            )
            timings["query_total"] = time.time() - t_query
            logger.info(f"Query ArcGIS: {len(rows)} righe in {timings['query_total']:.2f}s")
            
            # Fase 2: DataFrame
            t_df = time.time()
            df = dataframe_from_rows(rows, list(columns))
            timings["dataframe"] = time.time() - t_df
            
            # Fase 3: Salvataggio su blob DB (single entry, dispatch interno
            t_save = time.time()
            filename_stem = Path(output_filename).stem or "result"
            saved = save_pipeline_result_outcome(
                project_id=project_id,
                df=df,
                output_format=output_format,
                filename_stem=filename_stem,
                rows_count=len(rows),
                geom_column=geom_column if return_geometry else None,
                crs=layer_crs,
            )
            saved_path_display = saved.output_filename
            timings["save"] = time.time() - t_save

            timings["total"] = time.time() - inizio

            return {
                "success": True,
                "message": f"Query completata: {len(rows)} features estratte",
                "rows_count": len(rows),
                "columns": columns,
                "output_path": saved_path_display,
                "project_id": project_id,
                "timings": timings,
            }
            
        except Exception as e:
            logger.error(f"[GESTITO] Errore execute_query ArcGIS: {e}", exc_info=True)
            return {
                "success": False,
                "message": str(e),
                "rows_count": 0,
                "columns": [],
                "output_path": "",
                "timings": {},
            }
    
    # HELPER INTERNI
    
    def _to_connection_info(self, conn: ArcGISConnection) -> ArcGISConnectionInfo:
        """Converte ArcGISConnection (Pydantic) → ArcGISConnectionInfo (dataclass leggero)."""
        return ArcGISConnectionInfo(
            server_url=conn.server_url,
            server_type=conn.server_type,
            auth_type=conn.auth_type,
            username=conn.username,
            password=conn.password,
            client_id=conn.client_id,
            client_secret=conn.client_secret,
            token_url=conn.token_url or "",
        )
    
    def _get_layer_crs(self, connection_id: str, layer_url: str, out_sr: int = None) -> str:
        """
        Recupera il CRS del layer come stringa EPSG.
        
        Se out_sr è specificato, usa quello (l'utente ha richiesto una riproiezione).
        Altrimenti legge la spatialReference dallo schema del layer.
        
        Args:
            connection_id: ID connessione
            layer_url: URL del layer
            out_sr: SRID output esplicito (opzionale)
            
        Returns:
            Stringa CRS (es. "EPSG:32632"), fallback a "EPSG:4326"
        """
        connection = self.get_connection(connection_id)
        if not connection:
            return "EPSG:4326"

        conn_info = self._to_connection_info(connection)
        return self._operations.get_layer_crs(conn_info, layer_url, out_sr)
    
    # SCRITTURA: ADD / UPDATE / DELETE / CREATE SERVICE
    
    def write_features(
        self,
        connection_id: str,
        layer_url: str,
        operation: str,
        input_path: str,
        geometry_column: str = "geometry",
        object_ids: Optional[List[int]] = None,
        where: Optional[str] = None,
        source_srid: Optional[int] = None,
        batch_size: Optional[int] = None,
        match_column: str = "",
        column_mappings: ArcGISColumnMappingsInput = None,
    ) -> ArcGISFeatureWritePublicResult:
        """
        Scrive features su un layer ArcGIS esistente.
        
        Legge il file di output della pipeline, converte in Esri JSON
        e chiama add/update/delete.
        
        Args:
            connection_id: ID connessione ArcGIS
            layer_url: URL completo del layer (es. .../FeatureServer/0)
            operation: "add", "update", "delete"
            input_path: path del file sorgente (output pipeline)
            geometry_column: nome colonna geometria
            object_ids: per delete — lista OBJECTID
            where: per delete — clausola WHERE
            
        Returns:
            {success, message, count, errors}
        """
        connection = self.get_connection(connection_id)
        if not connection:
            raise ValueError(f"Connessione '{connection_id}' non trovata")
        
        conn_info = self._to_connection_info(connection)
        
        # DELETE legacy via OBJECTID/where non richiede file input.
        if operation == "delete" and not match_column:
            return self._writer.delete_features(
                conn_info,
                layer_url,
                object_ids=object_ids,
                where=where,
            )
        
        # ADD / UPDATE / UPSERT / DELETE-by-match: leggi file e converti.
        if not input_path:
            raise ValueError("input_path richiesto per operazioni add/update/upsert")
        
        df = self._load_dataframe(input_path)
        logger.info(f"Caricato file {input_path}: {len(df)} righe, colonne: {list(df.columns)}")
        
        return self._write_features_df_core(
            conn_info=conn_info,
            layer_url=layer_url,
            operation=operation,
            df=df,
            geometry_column=geometry_column,
            source_srid=source_srid,
            batch_size=batch_size,
            match_column=match_column,
            column_mappings=column_mappings,
        )
    
    def write_features_from_df(
        self,
        connection_id: str,
        layer_url: str,
        operation: str,
        df: FrameLike,
        geometry_column: str = "geometry",
        source_srid: Optional[int] = None,
        batch_size: Optional[int] = None,
        match_column: str = "",
        column_mappings: ArcGISColumnMappingsInput = None,
    ) -> ArcGISFeatureWritePublicResult:
        """
        Scrive features su un layer ArcGIS partendo da un DataFrame.
        Alternativa a write_features che evita il round-trip su file.

        Args:
            connection_id: ID connessione ArcGIS
            layer_url: URL completo del layer
            operation: "add" o "update"
            df: DataFrame (o GeoDataFrame) con le features
            geometry_column: nome colonna geometria
            source_srid: SRID sorgente (default 4326)
        """
        conn_info = self._resolve_write_connection(connection_id)
        return self._write_features_df_core(
            conn_info=conn_info,
            layer_url=layer_url,
            operation=operation,
            df=df,
            geometry_column=geometry_column,
            source_srid=source_srid,
            batch_size=batch_size,
            match_column=match_column,
            column_mappings=column_mappings,
        )

    def create_service(
        self,
        connection_id: str,
        service_name: str,
        layer_name: str,
        input_path: str,
        geometry_column: str = "geometry",
        source_srid: Optional[int] = None,
        folder_id: str = "",
        column_mappings: ArcGISColumnMappingsInput = None,
    ) -> ArcGISFeatureWritePublicResult:
        """
        Crea un nuovo Feature Service su ArcGIS Online con dati da file.

        Flusso:
        1. Legge il file di output pipeline
        2. Crea il servizio con schema derivato dal DataFrame
        3. Carica i dati

        Args:
            connection_id: ID connessione ArcGIS
            service_name: nome del nuovo servizio
            layer_name: nome del layer
            input_path: path del file sorgente
            geometry_column: nome colonna geometria

        Returns:
            {success, service_url, layer_url, feature_count}
        """
        df = self._load_dataframe(input_path)
        logger.info(f"Caricato file {input_path}: {len(df)} righe per nuovo servizio '{service_name}'")
        return self.create_service_from_df(
            connection_id=connection_id,
            service_name=service_name,
            layer_name=layer_name,
            df=df,
            geometry_column=geometry_column,
            source_srid=source_srid,
            folder_id=folder_id,
            column_mappings=column_mappings,
        )

    def create_service_from_df(
        self,
        connection_id: str,
        service_name: str,
        layer_name: str,
        df: FrameLike,
        geometry_column: str = "geometry",
        source_srid: Optional[int] = None,
        folder_id: str = "",
        column_mappings: ArcGISColumnMappingsInput = None,
    ) -> ArcGISFeatureWritePublicResult:
        """Crea un nuovo Feature Service da un DataFrame (senza file intermedio)."""
        conn_info = self._resolve_write_connection(connection_id)

        return self._writer.create_service_from_df(
            conn_info,
            service_name=service_name,
            layer_name=layer_name,
            df=df,
            geometry_column=geometry_column,
            source_srid=source_srid,
            folder_id=folder_id,
            column_mappings=column_mappings,
        )

    def _resolve_write_connection(self, connection_id: str) -> ArcGISConnectionInfo:
        connection = self.get_connection(connection_id)
        if not connection:
            raise ValueError(f"Connessione '{connection_id}' non trovata")
        return self._to_connection_info(connection)

    def _write_features_df_core(
        self,
        conn_info: ArcGISConnectionInfo,
        layer_url: str,
        operation: str,
        df: FrameLike,
        geometry_column: str,
        source_srid: Optional[int],
        batch_size: Optional[int],
        match_column: str,
        column_mappings: ArcGISColumnMappingsInput,
    ) -> ArcGISFeatureWritePublicResult:
        """Shared private write boundary for file-backed and DataFrame-backed writes."""
        return self._writer.write_features_df_core(
            conn_info=conn_info,
            layer_url=layer_url,
            operation=operation,
            df=df,
            geometry_column=geometry_column,
            source_srid=source_srid,
            batch_size=batch_size,
            match_column=match_column,
            column_mappings=column_mappings,
        )

    @staticmethod
    def _load_dataframe(file_path: str) -> FrameLike:
        """
        Carica un file in un DataFrame/GeoDataFrame.
        
        Supporta: CSV, GeoJSON, GeoPackage, Shapefile, JSON, Excel.
        Il CRS NON viene impostato qui — viene passato dal chiamante
        tramite source_srid (letto dallo schema del nodo sorgente).
        """
        return load_arcgis_feature_dataframe(file_path)


# FACTORY FUNCTION

def create_features_service(
    client: ArcGISProviderProtocol = None,
    arcgis_dir: Path = None,
    results_dir: Path = None,
) -> FeaturesService:
    """Factory function per creare un FeaturesService con Dependency Injection."""
    return FeaturesService(
        client=client,
        arcgis_dir=arcgis_dir,
        results_dir=results_dir,
    )
