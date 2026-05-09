"""
Servizio CRUD per connessioni ArcGIS Feature Service.

Keeps provider-specific behavior behind a narrow integration boundary.
"""

import json
import uuid
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple, Protocol

import pandas as pd

from app.config import settings
from app.utils.logger import get_logger
from app.utils.result_store import save_pipeline_result_outcome
from app.core.files.dataframe_io import _read_csv_safe
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

import app.db.pg_connection as db
from app.shared.crypto import encrypt as _encrypt, decrypt as _decrypt

DEFAULT_GROUP = "default"

# Campi sensibili cifrati a riposo: senza questa lista, password e
# client_secret verrebbero scritti in chiaro su arcgis_connections e
# chiunque leggesse il DB avrebbe le credenziali live. Usiamo lo stesso
# pattern di ConnectionService (app.shared.crypto Fernet, prefisso
# "enc:", idempotente
_SENSITIVE_FIELDS = ("password", "client_secret")


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"expected datetime or ISO string, got {type(value).__name__}")


def _ensure_default_group() -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO groups (id, name, description, created_at, updated_at) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
               (DEFAULT_GROUP, "Default", "Gruppo default", now, now))


def _row_to_connection(row: dict) -> ArcGISConnection:
    # decrypt e' idempotente su valori legacy in chiaro (nessun prefisso
    # "enc:"): li restituisce tal quali, permettendo una migrazione lazy
    return ArcGISConnection(
        id=row["id"],
        group_id=row.get("group_id") or DEFAULT_GROUP,
        name=row["name"],
        description=row.get("description") or "",
        server_url=row["server_url"],
        server_type=row.get("server_type") or "arcgis_server",
        auth_type=row.get("auth_type") or "token",
        username=row.get("username") or "",
        password=_decrypt(row.get("password") or "") or "",
        client_id=row.get("client_id") or "",
        client_secret=_decrypt(row.get("client_secret") or "") or "",
        token_url=row.get("token_url") or None,
        created_at=_as_datetime(row["created_at"]),
        updated_at=_as_datetime(row["updated_at"]),
        created_by=row.get("created_by"),
    )


def _generate_id(name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_uuid = str(uuid.uuid4())[:8]
    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_')).strip()
    safe_name = safe_name.replace(' ', '_')[:30]
    return f"arcgis_{safe_name}_{timestamp}_{short_uuid}"


def create_connection_db(data: ArcGISCreate, group_id: str = DEFAULT_GROUP,
                          created_by: Optional[str] = None) -> ArcGISConnection:
    _ensure_default_group()
    connection_id = _generate_id(data.name)
    now = datetime.now(timezone.utc).isoformat()

    # Auto-detect server_type
    server_type = data.server_type
    if not server_type:
        url = (data.server_url or "").lower()
        if "arcgis.com" in url:
            server_type = "arcgis_online"
        elif "portal" in url or "enterprise" in url:
            server_type = "arcgis_enterprise"
        else:
            server_type = "arcgis_server"

    # Cifra i campi sensibili prima dell'INSERT. encrypt e' idempotente
    enc_password = _encrypt(data.password or "") or ""
    enc_client_secret = _encrypt(data.client_secret or "") or ""

    db.execute("""
        INSERT INTO arcgis_connections
        (id, group_id, name, description, server_url, server_type, auth_type,
         username, password, client_id, client_secret, token_url, created_at, updated_at, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (connection_id, group_id, data.name, data.description or "",
          data.server_url, server_type, data.auth_type,
          data.username or "", enc_password,
          data.client_id or "", enc_client_secret,
          data.token_url or "", now, now, created_by))

    logger.info(f"Connessione ArcGIS creata: {connection_id} (group {group_id})", op="create")
    return _row_to_connection(dict(db.fetchone("SELECT * FROM arcgis_connections WHERE id = %s", (connection_id,))))


def list_connections_db(group_id: Optional[str] = None) -> ArcGISList:
    """
    Lista connessioni ArcGIS.
    - group_id None -> tutte (uso superadmin)
    - group_id "abc" -> solo del gruppo
    """
    if group_id is None:
        rows = db.fetchall("SELECT * FROM arcgis_connections ORDER BY updated_at DESC")
    else:
        rows = db.fetchall(
            "SELECT * FROM arcgis_connections WHERE group_id = %s ORDER BY updated_at DESC",
            (group_id,),
        )
    return ArcGISList(connections=[_row_to_connection(dict(r)) for r in rows])


def get_connection_db(connection_id: str) -> Optional[ArcGISConnection]:
    """
    Ritorna la connessione (senza filtro gruppo).
    Il group check va fatto dal chiamante con `assert_group_access`.
    """
    row = db.fetchone("SELECT * FROM arcgis_connections WHERE id = %s", (connection_id,))
    return _row_to_connection(dict(row)) if row else None


def update_connection_db(connection_id: str, data: ArcGISUpdate) -> Optional[ArcGISConnection]:
    if not db.fetchone("SELECT id FROM arcgis_connections WHERE id = %s", (connection_id,)):
        return None

    update_data = data.model_dump(exclude_unset=True)
    now = datetime.now(timezone.utc).isoformat()

    # Bugfix: il frontend riceve i campi sensibili mascherati come "***" nelle
    # list/get e li rispedisce inalterati se l'utente non li modifica
    # Skipparli per non sovrascrivere i valori reali con la maschera
    _SENSITIVE = {"password", "client_secret"}
    for sf in list(update_data.keys()):
        if sf in _SENSITIVE and update_data.get(sf) in (None, "", "***"):
            del update_data[sf]

    field_map = {
        "name": "name", "description": "description", "server_url": "server_url",
        "server_type": "server_type", "auth_type": "auth_type",
        "username": "username", "password": "password",
        "client_id": "client_id", "client_secret": "client_secret", "token_url": "token_url",
    }

    sets, params = [], []
    for payload_field, column_name in field_map.items():
        if payload_field in update_data:
            sets.append(f"{column_name} = %s")
            field_value = update_data[payload_field]
            # Cifratura at-rest per i campi sensibili (idempotente su valori
            # gia' cifrati grazie al prefisso "enc:" riconosciuto dal modulo
            # crypto
            if (
                payload_field in _SENSITIVE_FIELDS
                and isinstance(field_value, str)
                and field_value
            ):
                field_value = _encrypt(field_value)
            params.append(field_value)

    if not sets:
        return get_connection_db(connection_id)

    sets.append("updated_at = %s")
    params.extend([now, connection_id])
    db.execute(f"UPDATE arcgis_connections SET {', '.join(sets)} WHERE id = %s", params)
    return get_connection_db(connection_id)


def delete_connection_db(connection_id: str) -> bool:
    if not db.fetchone("SELECT id FROM arcgis_connections WHERE id = %s", (connection_id,)):
        return False
    db.execute("DELETE FROM arcgis_connections WHERE id = %s", (connection_id,))
    logger.info(f"Connessione ArcGIS eliminata: {connection_id}", op="delete")
    return True


# PROTOCOLLI PER DEPENDENCY INJECTION

class SaveFunctionProtocol(Protocol):
    """Protocollo per la funzione di salvataggio DataFrame — iniettabile per test."""
    def __call__(
        self,
        df: pd.DataFrame,
        output_path: Path,
        format: str,
        geom_column: Optional[str] = None,
        crs: str = "EPSG:4326"
    ) -> Path: ...


# SERVIZIO PRINCIPALE

class FeaturesService:
    """
    Servizio per gestione connessioni e operazioni su ArcGIS Feature Service.
    
    Combina il ruolo di ConnectionService (CRUD connessioni) con quello di
    DatabaseInspector (browse schema) e QueryExecutor (esecuzione query),
    adattati per il protocollo REST di ArcGIS.
    
    Dependency Injection:
    - client: ArcGISProviderProtocol (mockabile per test)
    - save_function: salvataggio DataFrame (mockabile per test)
    - arcgis_dir: directory storage connessioni
    - results_dir: directory output risultati
    """
    
    def __init__(
        self,
        client: ArcGISProviderProtocol = None,
        save_function: SaveFunctionProtocol = None,
        arcgis_dir: Path = None,
        results_dir: Path = None,
    ) -> None:
        self._client = client or create_arcgis_provider()
        self._operations = ArcGISFeatureOperations(self._client)
        self._writer = ArcGISFeatureWriteService(self._client)
        self._save = save_function or self._salvataggio_default
        self._arcgis_dir = arcgis_dir or settings.ARCGIS_CONNECTIONS_DIR
        self._results_dir = results_dir or Path(settings.RESULTS_DIR)
        
        # Crea directory se non esiste
    
    # CRUD CONNESSIONI
    
    def _generate_connection_id(self, name: str) -> str:
        """Genera ID univoco per connessione (stesso pattern di ConnectionService)."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_')).strip()
        safe_name = safe_name.replace(' ', '_')[:30]
        return f"arcgis_{safe_name}_{timestamp}_{short_uuid}"
    
    def _get_connection_path(self, connection_id: str) -> Path:
        """Ottieni path file connessione."""
        return self._arcgis_dir / f"{connection_id}.json"
    
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
    
    def browse_services(self, connection_id: str) -> List[dict]:
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
    
    def list_folders(self, connection_id: str) -> List[dict]:
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

    def search_services(self, connection_id: str, query: str = "", scope: str = "user") -> List[dict]:
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
    
    def browse_layers(self, connection_id: str, service_url: str) -> List[dict]:
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
    
    def get_layer_schema(self, connection_id: str, layer_url: str) -> dict:
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
    ) -> Dict[str, Any]:
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
            # Directory output
            project_dir = self._results_dir / project_id
            project_dir.mkdir(parents=True, exist_ok=True)
            
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
            df = pd.DataFrame(rows, columns=columns)
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
    
    @staticmethod
    def _detect_server_type(server_url: str) -> str:
        """
        Auto-detect tipo server dall'URL.
        
        - *.arcgis.com → arcgis_online
        - */portal/* → arcgis_enterprise
        - altro → arcgis_server
        """
        url = (server_url or "").lower()
        if "arcgis.com" in url:
            return "arcgis_online"
        if "/portal/" in url:
            return "arcgis_enterprise"
        return "arcgis_server"
    
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
    
    @staticmethod
    def _salvataggio_default(
        df: pd.DataFrame,
        output_path: Path,
        format: str,
        geom_column: Optional[str] = None,
        crs: str = "EPSG:4326"
    ) -> Path:
        """
        Salvataggio di fallback (formati base).
        
        In produzione viene sostituito da app.utils.dataframe.save_dataframe
        che supporta anche formati geospaziali (geojson, gpkg, shp).
        """
        final_path = output_path.with_suffix(f".{format}")
        if format == "csv":
            df.to_csv(final_path, index=False, encoding="utf-8")
        elif format == "json":
            df.to_json(final_path, orient="records", indent=2)
        elif format == "xlsx":
            df.to_excel(final_path, index=False)
        else:
            final_path = output_path.with_suffix(".csv")
            df.to_csv(final_path, index=False, encoding="utf-8")
        return final_path
    
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
    ) -> dict:
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
        df: pd.DataFrame,
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
    ) -> dict:
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
        df: pd.DataFrame,
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
        df: pd.DataFrame,
        geometry_column: str,
        source_srid: Optional[int],
        batch_size: Optional[int],
        match_column: str,
        column_mappings: ArcGISColumnMappingsInput,
    ) -> dict:
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

    def _resolve_objectids_for_update(
        self,
        conn_info: ArcGISConnectionInfo,
        layer_url: str,
        features: List[dict],
        match_column: str,
    ) -> List[dict]:
        """
        Risolve gli OBJECTID per update tramite colonna match.

        1. Raccoglie i valori della colonna match dal DataFrame
        2. Query il layer per ottenere OBJECTID + colonna match
        3. Crea un mapping match_value → OBJECTID
        4. Aggiunge OBJECTID a ogni feature

        Features senza match vengono scartate (log warning).
        """
        return self._writer.resolve_objectids_for_update(
            conn_info,
            layer_url,
            features,
            match_column,
        )

    @staticmethod
    def _load_dataframe(file_path: str) -> pd.DataFrame:
        """
        Carica un file in un DataFrame/GeoDataFrame.
        
        Supporta: CSV, GeoJSON, GeoPackage, Shapefile, JSON, Excel.
        Il CRS NON viene impostato qui — viene passato dal chiamante
        tramite source_srid (letto dallo schema del nodo sorgente).
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"File non trovato: {file_path}")
        
        suffix = path.suffix.lower()
        
        try:
            import geopandas as gpd
            
            if suffix == ".geojson":
                return gpd.read_file(path)
            elif suffix == ".gpkg":
                return gpd.read_file(path)
            elif suffix in (".shp",):
                return gpd.read_file(path)
        except ImportError:
            pass
        
        if suffix == ".csv":
            df = _read_csv_safe(path)
            # Cerca colonna geometria WKT
            geom_col = None
            if "geometry" in df.columns:
                geom_col = "geometry"

            if geom_col:
                try:
                    import geopandas as gpd
                    from shapely import wkt
                    df["geometry"] = df[geom_col].apply(
                        lambda wkt_value: (
                            wkt.loads(wkt_value) if pd.notna(wkt_value) else None
                        )
                    )
                    gdf = gpd.GeoDataFrame(df, geometry="geometry")
                    return gdf
                except (ImportError, Exception) as e:
                    logger.warning(f"Errore conversione geometria WKT: {e}")
                    pass
            return df
        elif suffix == ".json":
            return pd.read_json(path)
        elif suffix in (".xlsx", ".xls"):
            return pd.read_excel(path)
        else:
            # Estensione non riconosciuta: prova come GeoJSON via geopandas
            # Fallback a CSV solo se l'estensione e' plausibilmente CSV-like
            # tsv, .txt). Altrimenti fail-fast: un .gpkg corrotto o un
            # binario mislabel-ato non deve essere letto silenziosamente come CSV
            try:
                import geopandas as gpd
                return gpd.read_file(path)
            except Exception as exc:
                logger.warning(
                    f"Lettura come GeoJSON fallita per {path.name} "
                    f"(suffix={suffix!r}): {type(exc).__name__}: {exc}"
                )
                if suffix in (".tsv", ".txt"):
                    logger.info(
                        f"Fallback a CSV per {path.name}: suffix {suffix!r} plausibile"
                    )
                    return _read_csv_safe(path)
                raise ValueError(
                    f"Formato file non supportato: {path.name} (suffix={suffix!r}). "
                    f"Formati supportati: .csv, .tsv, .txt, .geojson, .gpkg, .shp, "
                    f".json, .xlsx, .xls. Lettura GeoJSON fallita: {exc}"
                ) from exc


# FACTORY FUNCTION

def create_features_service(
    client: ArcGISProviderProtocol = None,
    save_function: SaveFunctionProtocol = None,
    arcgis_dir: Path = None,
    results_dir: Path = None,
) -> FeaturesService:
    """Factory function per creare un FeaturesService con Dependency Injection."""
    return FeaturesService(
        client=client,
        save_function=save_function,
        arcgis_dir=arcgis_dir,
        results_dir=results_dir,
    )
