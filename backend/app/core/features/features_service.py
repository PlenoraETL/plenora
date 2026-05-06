"""
Servizio CRUD per connessioni ArcGIS Feature Service.

Architettura
============
Servizio parallelo a ConnectionService per gestire le connessioni ad
ArcGIS Server/Enterprise/Online. Le connessioni sono persistite nella
tabella PostgreSQL `arcgis_connections` tramite `app.db.db_service`,
seguendo lo stesso pattern di ConnectionService post-migrazione PG.
I campi sensibili (`password`, `client_secret`) sono cifrati a riposo
via `app.shared.crypto` (Fernet, prefisso "enc:", idempotente).

Operazioni CRUD:
- list_connections(): lista connessioni ArcGIS
- get_connection(): recupera per ID
- create_connection(): crea nuova
- update_connection(): aggiorna
- delete_connection(): elimina
- test_connection(): testa connettività

Operazioni di browse (delega a ArcGISClient):
- browse_services(): lista Feature Service disponibili
- browse_layers(): lista layer di un servizio
- get_layer_schema(): schema campi di un layer

Operazioni dati (delega a ArcGISClient):
- query_layer(): query features → (righe, colonne) — compatibile con QueryExecutor
- execute_query(): query completa con salvataggio file — compatibile con ESTRAI
- write_features(): scrittura su Feature Service
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
from app.utils.result_store import save_pipeline_result
from app.utils.dataframe import _read_csv_safe
from app.core.features.models import (
    ArcGISConnection,
    ArcGISCreate,
    ArcGISUpdate,
    ArcGISList,
    ArcGISTestResult,
)
from app.core.features.arcgis_client import (
    ArcGISClient,
    ArcGISConnectionInfo,
    EsriJsonConverter,
    create_client,
)

logger = get_logger(__name__)

# =============================================================================
# CRUD STORAGE — tabella arcgis_connections su PostgreSQL
# =============================================================================
# Merged da features_service_db.py (S4 consolidation). Le 5 funzioni
# pubbliche *_db e i 3 helper privati sono mantenuti come module-level
# per preservare la superficie importata dai test.

import app.db.pg_connection as db
from app.shared.crypto import encrypt as _encrypt, decrypt as _decrypt

DEFAULT_GROUP = "default"

# Campi sensibili cifrati a riposo: senza questa lista, password e
# client_secret verrebbero scritti in chiaro su arcgis_connections e
# chiunque leggesse il DB avrebbe le credenziali live. Usiamo lo stesso
# pattern di ConnectionService (app.shared.crypto Fernet, prefisso
# "enc:", idempotente).
_SENSITIVE_FIELDS = ("password", "client_secret")


def _ensure_default_group():
    now = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO groups (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
               (DEFAULT_GROUP, "Default", "Gruppo default", now, now))


def _row_to_connection(row: dict) -> ArcGISConnection:
    # decrypt() e' idempotente su valori legacy in chiaro (nessun prefisso
    # "enc:"): li restituisce tal quali, permettendo una migrazione lazy.
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
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        created_by=row.get("created_by"),
    )


def _generate_id(name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_uuid = str(uuid.uuid4())[:8]
    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_')).strip()
    safe_name = safe_name.replace(' ', '_')[:30]
    return f"arcgis_{safe_name}_{timestamp}_{short_uuid}"


def create_connection_db(data: ArcGISCreate, group_id: str = DEFAULT_GROUP,
                          created_by: str = None) -> ArcGISConnection:
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

    # Cifra i campi sensibili prima dell'INSERT. encrypt() e' idempotente.
    enc_password = _encrypt(data.password or "") or ""
    enc_client_secret = _encrypt(data.client_secret or "") or ""

    db.execute("""
        INSERT INTO arcgis_connections
        (id, group_id, name, description, server_url, server_type, auth_type,
         username, password, client_id, client_secret, token_url, created_at, updated_at, created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (connection_id, group_id, data.name, data.description or "",
          data.server_url, server_type, data.auth_type,
          data.username or "", enc_password,
          data.client_id or "", enc_client_secret,
          data.token_url or "", now, now, created_by))

    logger.info(f"Connessione ArcGIS creata: {connection_id} (group {group_id})", op="create")
    return _row_to_connection(dict(db.fetchone("SELECT * FROM arcgis_connections WHERE id = ?", (connection_id,))))


def list_connections_db(group_id: Optional[str] = None) -> ArcGISList:
    """
    Lista connessioni ArcGIS.
    - group_id None     -> tutte (uso superadmin)
    - group_id "abc"    -> solo del gruppo
    """
    if group_id is None:
        rows = db.fetchall("SELECT * FROM arcgis_connections ORDER BY updated_at DESC")
    else:
        rows = db.fetchall(
            "SELECT * FROM arcgis_connections WHERE group_id = ? ORDER BY updated_at DESC",
            (group_id,),
        )
    return ArcGISList(connections=[_row_to_connection(dict(r)) for r in rows])


def get_connection_db(connection_id: str) -> Optional[ArcGISConnection]:
    """
    Ritorna la connessione (senza filtro gruppo).
    Il group check va fatto dal chiamante con `assert_group_access`.
    """
    row = db.fetchone("SELECT * FROM arcgis_connections WHERE id = ?", (connection_id,))
    return _row_to_connection(dict(row)) if row else None


def update_connection_db(connection_id: str, data: ArcGISUpdate) -> Optional[ArcGISConnection]:
    if not db.fetchone("SELECT id FROM arcgis_connections WHERE id = ?", (connection_id,)):
        return None

    update_data = data.model_dump(exclude_unset=True)
    now = datetime.now(timezone.utc).isoformat()

    # Bugfix: il frontend riceve i campi sensibili mascherati come "***" nelle
    # list/get e li rispedisce inalterati se l'utente non li modifica.
    # Skipparli per non sovrascrivere i valori reali con la maschera.
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
    for pf, col in field_map.items():
        if pf in update_data:
            sets.append(f"{col} = ?")
            val = update_data[pf]
            # Cifratura at-rest per i campi sensibili (idempotente su valori
            # gia' cifrati grazie al prefisso "enc:" riconosciuto dal modulo
            # crypto).
            if pf in _SENSITIVE_FIELDS and isinstance(val, str) and val:
                val = _encrypt(val)
            params.append(val)

    if not sets:
        return get_connection_db(connection_id)

    sets.append("updated_at = ?")
    params.extend([now, connection_id])
    db.execute(f"UPDATE arcgis_connections SET {', '.join(sets)} WHERE id = ?", params)
    return get_connection_db(connection_id)


def delete_connection_db(connection_id: str) -> bool:
    if not db.fetchone("SELECT id FROM arcgis_connections WHERE id = ?", (connection_id,)):
        return False
    db.execute("DELETE FROM arcgis_connections WHERE id = ?", (connection_id,))
    logger.info(f"Connessione ArcGIS eliminata: {connection_id}", op="delete")
    return True


# =============================================================================
# PROTOCOLLI PER DEPENDENCY INJECTION
# =============================================================================

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


# =============================================================================
# SERVIZIO PRINCIPALE
# =============================================================================

class FeaturesService:
    """
    Servizio per gestione connessioni e operazioni su ArcGIS Feature Service.
    
    Combina il ruolo di ConnectionService (CRUD connessioni) con quello di
    DatabaseInspector (browse schema) e QueryExecutor (esecuzione query),
    adattati per il protocollo REST di ArcGIS.
    
    Dependency Injection:
    - client: ArcGISClient (mockabile per test)
    - save_function: salvataggio DataFrame (mockabile per test)
    - arcgis_dir: directory storage connessioni
    - results_dir: directory output risultati
    """
    
    def __init__(
        self,
        client: ArcGISClient = None,
        save_function: SaveFunctionProtocol = None,
        arcgis_dir: Path = None,
        results_dir: Path = None,
    ):
        self._client = client or create_client()
        self._save = save_function or self._salvataggio_default
        self._arcgis_dir = arcgis_dir or settings.ARCGIS_CONNECTIONS_DIR
        self._results_dir = results_dir or Path(settings.RESULTS_DIR)
        
        # Crea directory se non esiste
    
    # =========================================================================
    # CRUD CONNESSIONI
    # =========================================================================
    
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
                          created_by: str = None) -> ArcGISConnection:
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
        result = self._client.test_connection(conn_info)
        
        return ArcGISTestResult(
            success=result["success"],
            message=result["message"],
            details=result.get("details"),
        )
    
    # =========================================================================
    # BROWSE: SERVIZI E LAYER
    # =========================================================================
    
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
        return self._client.list_services(conn_info)
    
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
        return self._client.list_folders(conn_info)

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
        
        # Per Online usa la Search API
        if self._client._is_arcgis_online(conn_info):
            return self._client.search_services_online(conn_info, query=query, scope=scope)
        
        # Per Server/Enterprise, browse normale
        return self._client.list_services(conn_info)
    
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
        return self._client.list_layers(conn_info, service_url)
    
    def get_layer_schema(self, connection_id: str, layer_url: str) -> dict:
        """
        Recupera lo schema di un layer (campi + metadata).
        
        Equivalente a DatabaseInspector.get_tables_and_columns() per un
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
        return self._client.get_layer_schema(conn_info, layer_url)
    
    # =========================================================================
    # QUERY (lettura dati)
    # =========================================================================
    
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
        
        Interfaccia identica a QueryExecutor.execute_raw() per
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
        
        return self._client.query_features(
            conn_info, layer_url,
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
        
        Interfaccia identica a QueryExecutor.execute_query() per
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
            
            # Fase 0: Recupera CRS dal layer (per salvataggio geo corretto)
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
            
            # Fase 3: Salvataggio su blob DB (single entry, dispatch interno)
            t_save = time.time()
            filename_stem = Path(output_filename).stem or "result"
            saved = save_pipeline_result(
                project_id=project_id,
                df=df,
                output_format=output_format,
                filename_stem=filename_stem,
                rows_count=len(rows),
                geom_column=geom_column if return_geometry else None,
                crs=layer_crs,
            )
            saved_path_display = saved["output_filename"]
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
    
    # =========================================================================
    # HELPER INTERNI
    # =========================================================================
    
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
        # Se l'utente ha specificato un output SR, usa quello
        if out_sr:
            return f"EPSG:{out_sr}"
        
        try:
            connection = self.get_connection(connection_id)
            if not connection:
                return "EPSG:4326"
            
            conn_info = self._to_connection_info(connection)
            schema = self._client.get_layer_schema(conn_info, layer_url)
            
            spatial_ref = schema.get("spatial_reference", {})
            # ArcGIS usa "wkid" o "latestWkid"
            wkid = spatial_ref.get("latestWkid") or spatial_ref.get("wkid")
            
            if wkid:
                logger.info(f"CRS layer: EPSG:{wkid}")
                return f"EPSG:{wkid}"
            
        except Exception as e:
            logger.warning(f"Impossibile determinare CRS del layer: {e}")
        
        return "EPSG:4326"
    
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
    
    # =========================================================================
    # SCRITTURA: ADD / UPDATE / DELETE / CREATE SERVICE
    # =========================================================================
    
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
        
        # DELETE non richiede file input
        if operation == "delete":
            result = self._client.delete_features(
                conn_info, layer_url, 
                object_ids=object_ids, where=where
            )
            return {
                "success": result["success"],
                "message": f"Eliminati {result['deleted_count']} features",
                "count": result["deleted_count"],
                "errors": result.get("errors", []),
            }
        
        # ADD / UPDATE: leggi file e converti
        if not input_path:
            raise ValueError("input_path richiesto per operazioni add/update")
        
        df = self._load_dataframe(input_path)
        logger.info(f"Caricato file {input_path}: {len(df)} righe, colonne: {list(df.columns)}")
        
        # Imposta CRS se fornito (dal nodo sorgente della pipeline)
        if source_srid and hasattr(df, 'set_crs') and (df.crs is None):
            import geopandas as gpd
            df = df.set_crs(epsg=source_srid)
            logger.info(f"CRS impostato da source_srid: EPSG:{source_srid}")
        
        # Converti in Esri JSON
        features = self._client.dataframe_to_esri_features(
            df, geometry_column=geometry_column, operation=operation
        )

        if not features:
            return {
                "success": False,
                "message": "Nessuna feature da caricare",
                "count": 0,
                "errors": [],
            }
        
        if operation == "add":
            result = self._client.add_features(conn_info, layer_url, features)
            count_key = "added_count"
            action = "Aggiunte"
        elif operation == "update":
            result = self._client.update_features(conn_info, layer_url, features)
            count_key = "updated_count"
            action = "Aggiornate"
        else:
            raise ValueError(f"Operazione non supportata: {operation}")
        
        return {
            "success": result["success"],
            "message": f"{action} {result[count_key]} features su {len(features)} totali",
            "count": result[count_key],
            "errors": result.get("errors", []),
        }
    
    def write_features_from_df(
        self,
        connection_id: str,
        layer_url: str,
        operation: str,
        df,
        geometry_column: str = "geometry",
        source_srid: Optional[int] = None,
        batch_size: int = None,
        match_column: str = "",
    ) -> dict:
        """
        Scrive features su un layer ArcGIS partendo da un DataFrame.
        Alternativa a write_features() che evita il round-trip su file.

        Args:
            connection_id: ID connessione ArcGIS
            layer_url: URL completo del layer
            operation: "add" o "update"
            df: DataFrame (o GeoDataFrame) con le features
            geometry_column: nome colonna geometria
            source_srid: SRID sorgente (default 4326)
        """
        connection = self.get_connection(connection_id)
        if not connection:
            raise ValueError(f"Connessione '{connection_id}' non trovata")

        conn_info = self._to_connection_info(connection)

        # Imposta CRS se necessario
        if source_srid and hasattr(df, 'set_crs') and getattr(df, 'crs', None) is None:
            import geopandas as gpd
            df = df.set_crs(epsg=source_srid)

        # Converti in Esri JSON
        features = self._client.dataframe_to_esri_features(
            df, geometry_column=geometry_column, operation=operation
        )

        if not features:
            return {
                "success": False,
                "message": "Nessuna feature da caricare",
                "count": 0,
                "errors": [],
            }

        if operation == "add":
            result = self._client.add_features(conn_info, layer_url, features, batch_size=batch_size)
            return self._write_result("Aggiunte", result, "added_count", len(features))

        elif operation == "update":
            if match_column:
                features = self._resolve_objectids_for_update(
                    conn_info, layer_url, features, match_column
                )
                if not features:
                    return {
                        "success": False,
                        "message": f"Nessuna feature matchata per colonna '{match_column}'",
                        "count": 0, "errors": [],
                    }
            result = self._client.update_features(conn_info, layer_url, features, batch_size=batch_size)
            return self._write_result("Aggiornate", result, "updated_count", len(features))

        elif operation == "upsert":
            if not match_column:
                raise ValueError("Colonna match obbligatoria per upsert")
            # Separa features: quelle con match → update, le altre → add
            to_update, to_add = self._split_upsert_features(
                conn_info, layer_url, features, match_column
            )
            total_updated = 0
            total_added = 0
            all_errors = []

            if to_update:
                res_upd = self._client.update_features(conn_info, layer_url, to_update, batch_size=batch_size)
                total_updated = res_upd.get("updated_count", 0)
                all_errors.extend(res_upd.get("errors", []))

            if to_add:
                res_add = self._client.add_features(conn_info, layer_url, to_add, batch_size=batch_size)
                total_added = res_add.get("added_count", 0)
                all_errors.extend(res_add.get("errors", []))

            total = total_updated + total_added
            return {
                "success": total > 0,
                "message": f"Upsert: {total_updated} aggiornate, {total_added} inserite su {len(features)} totali",
                "count": total,
                "feature_count": total,
                "updated": total_updated,
                "added": total_added,
                "errors": all_errors,
            }

        elif operation == "delete":
            if not match_column:
                raise ValueError("Colonna match obbligatoria per delete")
            # Risolvi OBJECTID per i valori match e cancella
            oid_features = self._resolve_objectids_for_update(
                conn_info, layer_url, features, match_column
            )
            if not oid_features:
                return {
                    "success": False,
                    "message": f"Nessuna feature trovata per colonna '{match_column}'",
                    "count": 0, "errors": [],
                }
            object_ids = [f["attributes"]["OBJECTID"] for f in oid_features]
            result = self._client.delete_features(conn_info, layer_url, object_ids=object_ids)
            return {
                "success": result["success"],
                "message": f"Eliminate {result['deleted_count']} features",
                "count": result["deleted_count"],
                "feature_count": result["deleted_count"],
                "errors": result.get("errors", []),
            }

        else:
            raise ValueError(f"Operazione non supportata: {operation}")

    def create_service(
        self,
        connection_id: str,
        service_name: str,
        layer_name: str,
        input_path: str,
        geometry_column: str = "geometry",
        source_srid: Optional[int] = None,
        folder_id: str = "",
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
        connection = self.get_connection(connection_id)
        if not connection:
            raise ValueError(f"Connessione '{connection_id}' non trovata")
        
        conn_info = self._to_connection_info(connection)
        
        df = self._load_dataframe(input_path)
        logger.info(f"Caricato file {input_path}: {len(df)} righe per nuovo servizio '{service_name}'")
        
        # Imposta CRS se fornito (dal nodo sorgente della pipeline)
        if source_srid and hasattr(df, 'set_crs') and (df.crs is None):
            import geopandas as gpd
            df = df.set_crs(epsg=source_srid)
            logger.info(f"CRS impostato da source_srid: EPSG:{source_srid}")
        
        result = self._client.create_service(
            conn_info,
            service_name=service_name,
            layer_name=layer_name,
            df=df,
            geometry_column=geometry_column,
            folder_id=folder_id,
        )
        
        return result

    def create_service_from_df(
        self,
        connection_id: str,
        service_name: str,
        layer_name: str,
        df,
        geometry_column: str = "geometry",
        source_srid: Optional[int] = None,
        folder_id: str = "",
    ) -> dict:
        """Crea un nuovo Feature Service da un DataFrame (senza file intermedio)."""
        connection = self.get_connection(connection_id)
        if not connection:
            raise ValueError(f"Connessione '{connection_id}' non trovata")

        conn_info = self._to_connection_info(connection)

        if source_srid and hasattr(df, 'set_crs') and (getattr(df, 'crs', None) is None):
            import geopandas as gpd
            df = df.set_crs(epsg=source_srid)

        return self._client.create_service(
            conn_info,
            service_name=service_name,
            layer_name=layer_name,
            df=df,
            geometry_column=geometry_column,
            folder_id=folder_id,
        )

    @staticmethod
    def _write_result(action: str, result: dict, count_key: str, total: int) -> dict:
        count = result.get(count_key, 0)
        return {
            "success": result["success"],
            "message": f"{action} {count} features su {total} totali",
            "count": count,
            "feature_count": count,
            "errors": result.get("errors", []),
        }

    def _split_upsert_features(
        self,
        conn_info,
        layer_url: str,
        features: List[dict],
        match_column: str,
    ) -> tuple:
        """
        Separa features in (to_update, to_add) per operazione upsert.

        - to_update: features con match nel layer (con OBJECTID assegnato)
        - to_add: features senza match nel layer (nuove)
        """
        import requests as _requests

        match_values = []
        for f in features:
            val = f.get("attributes", {}).get(match_column)
            if val is not None:
                match_values.append(val)

        if not match_values:
            return [], features

        token = self._client.authenticate(conn_info)
        referer_headers = {"Referer": conn_info.server_url}

        CHUNK = 200
        oid_map = {}

        for i in range(0, len(match_values), CHUNK):
            chunk = match_values[i:i + CHUNK]
            if isinstance(chunk[0], str):
                vals_str = ",".join(f"'{v}'" for v in chunk)
            else:
                vals_str = ",".join(str(v) for v in chunk)

            params = {
                "where": f"{match_column} IN ({vals_str})",
                "outFields": f"OBJECTID,{match_column}",
                "returnGeometry": "false",
                "f": "json",
            }
            if token:
                params["token"] = token

            try:
                resp = self._client._http.get(
                    f"{layer_url}/query", params=params, timeout=30,
                    headers=referer_headers,
                )
                data = resp.json()
                for feat in data.get("features", []):
                    attrs = feat.get("attributes", {})
                    oid = attrs.get("OBJECTID") or attrs.get("objectid")
                    match_val = attrs.get(match_column)
                    if oid is not None and match_val is not None:
                        oid_map[match_val] = oid
            except Exception as e:
                logger.error(f"Errore query OBJECTID per upsert: {e}", exc_info=True)

        to_update = []
        to_add = []
        for f in features:
            match_val = f.get("attributes", {}).get(match_column)
            oid = oid_map.get(match_val)
            if oid is not None:
                f["attributes"]["OBJECTID"] = oid
                to_update.append(f)
            else:
                # Rimuovi OBJECTID se presente (per add)
                f["attributes"].pop("OBJECTID", None)
                f["attributes"].pop("objectid", None)
                to_add.append(f)

        logger.info(f"Upsert split: {len(to_update)} da aggiornare, {len(to_add)} da inserire")
        return to_update, to_add

    def _resolve_objectids_for_update(
        self,
        conn_info,
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
        import requests as _requests

        # Raccogli valori match dal payload
        match_values = []
        for f in features:
            val = f.get("attributes", {}).get(match_column)
            if val is not None:
                match_values.append(val)

        if not match_values:
            logger.warning(f"Nessun valore trovato per colonna match '{match_column}'")
            return []

        token = self._client.authenticate(conn_info)
        referer_headers = {"Referer": conn_info.server_url}

        # Query a blocchi di 200 valori
        CHUNK = 200
        oid_map = {}  # match_value → OBJECTID

        for i in range(0, len(match_values), CHUNK):
            chunk = match_values[i:i + CHUNK]
            # Costruisci WHERE: stringhe con apici, numeri senza
            if isinstance(chunk[0], str):
                vals_str = ",".join(f"'{v}'" for v in chunk)
            else:
                vals_str = ",".join(str(v) for v in chunk)

            where = f"{match_column} IN ({vals_str})"

            params = {
                "where": where,
                "outFields": f"OBJECTID,{match_column}",
                "returnGeometry": "false",
                "f": "json",
            }
            if token:
                params["token"] = token

            try:
                resp = self._client._http.get(
                    f"{layer_url}/query", params=params, timeout=30,
                    headers=referer_headers,
                )
                data = resp.json()
                for feat in data.get("features", []):
                    attrs = feat.get("attributes", {})
                    oid = attrs.get("OBJECTID") or attrs.get("objectid")
                    match_val = attrs.get(match_column)
                    if oid is not None and match_val is not None:
                        oid_map[match_val] = oid
            except Exception as e:
                logger.error(f"Errore query OBJECTID per match: {e}", exc_info=True)

        logger.info(f"Risolti {len(oid_map)} OBJECTID su {len(match_values)} valori match")

        # Associa OBJECTID alle features
        resolved = []
        skipped = 0
        for f in features:
            match_val = f.get("attributes", {}).get(match_column)
            oid = oid_map.get(match_val)
            if oid is not None:
                f["attributes"]["OBJECTID"] = oid
                resolved.append(f)
            else:
                skipped += 1

        if skipped:
            logger.warning(f"Scartate {skipped} features senza match OBJECTID")

        return resolved

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
                    df["geometry"] = df[geom_col].apply(lambda x: wkt.loads(x) if pd.notna(x) else None)
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
            # Estensione non riconosciuta: prova come GeoJSON via geopandas.
            # Fallback a CSV solo se l'estensione e' plausibilmente CSV-like
            # (.tsv, .txt). Altrimenti fail-fast: un .gpkg corrotto o un
            # binario mislabel-ato non deve essere letto silenziosamente come CSV.
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


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_features_service(
    client: ArcGISClient = None,
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