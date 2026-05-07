"""
Router API per il modulo CARICA.

Architettura
============
Questo router espone gli endpoint per la configurazione e l'esecuzione
del modulo CARICA. Il frontend li usa per:

1. Ottenere la lista schema del database destinazione
2. Ottenere la lista tabelle in uno schema
3. Ottenere le colonne di una tabella esistente
4. Generare l'auto-mapping colonne
5. Validare la configurazione prima dell'esecuzione
6. Verificare conflitti in modalità APPEND

L'esecuzione vera e propria avviene:
- Sincrona: tramite l'endpoint execute_pipeline in main.py (che chiama l'orchestrator)
- Asincrona: tramite POST /api/tasks con type="carica" (task Celery)

Endpoints:
- GET    /api/carica/schemas/{connection_id}            → lista schema
- GET    /api/carica/tables/{connection_id}/{schema}    → lista tabelle
- GET    /api/carica/columns/{connection_id}/{schema}/{table} → colonne tabella
- POST   /api/carica/auto-mapping                       → genera mapping automatico
- POST   /api/carica/validate                           → valida configurazione
- POST   /api/carica/check-conflicts                    → pre-check conflitti

Integrazione: usa CaricaExecutor per logica, ConnectionService per connessioni.
"""

from app.utils.logger import get_logger
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.core.auth.dependencies import get_current_user, require_not_esecutore
from app.shared.contracts.auth import CurrentUser
from app.core.auth.ownership import assert_resource_visible
from app.core.carica.executor import CaricaExecutor, _dict_to_conn_obj
from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.column_mapper import ColumnMapper
from app.core.carica.ddl_generator import DDLGenerator
from app.core.carica.models import WriteConfig
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http


logger = get_logger("app.core.carica.router")

router = APIRouter(prefix="/carica", tags=["carica"])

# Servizi inizializzati da main.py
_connection_service = None
_executor = None


def init_carica_service(connection_service):
    """Inizializza il servizio CARICA. Chiamare da main.py dopo setup."""
    global _connection_service, _executor
    _connection_service = connection_service
    _executor = CaricaExecutor(connection_service=connection_service)


# MODELLI REQUEST / RESPONSE

class AutoMappingRequest(BaseModel):
    """Richiesta per auto-mapping colonne."""
    connection_id: str
    schema_name: str = "public"
    table_name: str = ""
    project_id: str = ""
    srid: int = 4326
    # Le colonne del DataFrame vengono passate come lista
    # (il DataFrame vero non è serializzabile via API)
    source_columns: List[Dict[str, str]] = []


class ValidateRequest(BaseModel):
    """Richiesta per validazione configurazione."""
    connection_id: str
    node_data: Dict[str, Any]
    source_columns: List[Dict[str, str]] = []
    total_rows: int = 0


class ConflictCheckRequest(BaseModel):
    """Richiesta per check conflitti."""
    connection_id: str
    schema_name: str
    table_name: str
    match_columns: List[str]
    project_id: Optional[str] = None
    df_match_columns: Optional[List[str]] = None
    sample_values: List[Dict[str, Any]] = []
    # Sorgente diretta: il backend interroga il DB sorgente senza esecuzione previa
    source_connection_id: Optional[str] = None
    source_schema: Optional[str] = None
    source_table: Optional[str] = None
    source_match_columns: Optional[List[str]] = None


class SchemaListResponse(BaseModel):
    """Risposta lista schema."""
    schemas: List[str]
    db_type: str


class TableListResponse(BaseModel):
    """Risposta lista tabelle."""
    tables: List[str]
    schema_name: str


class ColumnListResponse(BaseModel):
    """Risposta lista colonne."""
    columns: List[Dict[str, str]]
    table_name: str


# ENDPOINT: LISTA SCHEMA

@router.get("/schemas/{connection_id}")
async def get_schemas(connection_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """GET /api/carica/schemas/{connection_id}

    Ritorna la lista degli schema disponibili nel database.
    Per PostgreSQL/MSSQL: schema (public, dbo, custom)
    Per Oracle: schema utente
    Per MySQL/MariaDB: database
    """
    # SECURITY: verifica che l'utente possa vedere questa connessione
    # (owned dal suo gruppo o shared). Senza questo check un utente di
    # gruppoA potrebbe enumerare schemi/tabelle del DB di gruppoB.
    from app.services.connection_service import ConnectionService as _CS
    _conn_obj = _CS().get_connection(connection_id)
    assert_resource_visible(_conn_obj, "connections", current_user, "Connessione")
    try:
        connection = _get_connection(connection_id)
        db_type = connection.get("type", "postgres")
        engine = _create_engine(connection)

        config = WriteConfig(connection_id=connection_id)
        writer = DatabaseWriter(config=config, engine=engine, db_type=db_type)
        schemas = writer.get_schemas()

        return {"schemas": schemas, "db_type": db_type}

    except Exception as e:
        logger.error(f"[CRASH] Errore recupero schema: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# ENDPOINT: LISTA TABELLE

@router.get("/tables/{connection_id}/{schema_name}")
async def get_tables(connection_id: str, schema_name: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """GET /api/carica/tables/{connection_id}/{schema_name}

    Ritorna la lista delle tabelle nello schema specificato.
    """
    from app.services.connection_service import ConnectionService as _CS
    _conn_obj = _CS().get_connection(connection_id)
    assert_resource_visible(_conn_obj, "connections", current_user, "Connessione")
    try:
        connection = _get_connection(connection_id)
        db_type = connection.get("type", "postgres")
        engine = _create_engine(connection)

        config = WriteConfig(connection_id=connection_id, schema_name=schema_name)
        writer = DatabaseWriter(config=config, engine=engine, db_type=db_type)
        tables = writer.get_tables(schema_name)

        return {"tables": tables, "schema_name": schema_name}

    except Exception as e:
        logger.error(f"[CRASH] Errore recupero tabelle: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# ENDPOINT: COLONNE TABELLA

@router.get("/columns/{connection_id}/{schema_name}/{table_name}")
async def get_columns(connection_id: str, schema_name: str, table_name: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """GET /api/carica/columns/{connection_id}/{schema_name}/{table_name}

    Ritorna le colonne (nome + tipo) della tabella specificata.
    Usato dal frontend per popolare il form di mapping colonne.
    """
    from app.services.connection_service import ConnectionService as _CS
    _conn_obj = _CS().get_connection(connection_id)
    assert_resource_visible(_conn_obj, "connections", current_user, "Connessione")
    try:
        connection = _get_connection(connection_id)
        db_type = connection.get("type", "postgres")
        engine = _create_engine(connection)

        config = WriteConfig(
            connection_id=connection_id,
            schema_name=schema_name,
            table_name=table_name,
        )
        writer = DatabaseWriter(config=config, engine=engine, db_type=db_type)
        columns = writer.get_existing_columns()

        return {
            "columns": columns,
            "table_name": table_name,
            "table_exists": len(columns) > 0,
        }

    except Exception as e:
        logger.error(f"[CRASH] Errore recupero colonne: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# ENDPOINT: AUTO-MAPPING

@router.post("/auto-mapping")
async def auto_mapping(request: AutoMappingRequest, current_user: CurrentUser = Depends(require_not_esecutore)):
    """POST /api/carica/auto-mapping

    Genera automaticamente il mapping colonne sorgente → destinazione.
    Il frontend invia la lista colonne sorgente (dal nodo Result) e
    riceve il mapping proposto che l'utente può modificare.
    """
    try:
        import pandas as pd
        from app.shared.column_types import (
            canvas_type_to_pandas_dtype,
            metadata_to_dict,
        )

        # Ricostruisci un DataFrame vuoto con le colonne sorgente
        # (per il mapping servono solo i nomi e i tipi). La dispatch
        # canvas-type -> pandas dtype vive in app.shared.column_types
        # (fonte unica: alias, geometry, fallback object).
        columns_data = {
            col_info.get("name", ""): pd.Series(
                dtype=canvas_type_to_pandas_dtype(col_info.get("type", "str"))
            )
            for col_info in request.source_columns
        }

        df = pd.DataFrame(columns_data)

        # Canvas types (dict form del tipo semantico) per rilevare geometrie.
        # `source_columns` arriva gia' come list[{"name","type"}] (columns_metadata
        # shape) — l'adapter fa la sola conversione di forma.
        canvas_types = metadata_to_dict(request.source_columns)

        # Recupera colonne esistenti se la tabella è specificata
        existing_columns = []
        if request.table_name:
            try:
                connection = _get_connection(request.connection_id)
                db_type = connection.get("type", "postgres")
                engine = _create_engine(connection)
                ddl = DDLGenerator(db_type)
                query, params = ddl.query_table_columns(request.schema_name, request.table_name)

                from sqlalchemy import text
                with engine.connect() as conn:
                    result = conn.execute(text(query), params)
                    existing_columns = [
                        {"name": row[0], "type": str(row[1])}
                        for row in result
                    ]
            except Exception as e:
                logger.warning(f"Tabella non trovata per auto-mapping: {e}")

        # Genera mapping
        connection = _get_connection(request.connection_id)
        db_type = connection.get("type", "postgres")
        mapper = ColumnMapper(db_type)
        mappings = mapper.auto_map(
            df,
            existing_columns=existing_columns,
            canvas_types=canvas_types,
            srid=request.srid,
        )

        # Suggerisci match columns
        suggested_match = mapper.suggest_match_columns(mappings, existing_columns)

        return {
            "mappings": [
                {
                    "source_column": m.source_column,
                    "dest_column": m.dest_column,
                    "dest_type": m.dest_type,
                    "is_geometry": m.is_geometry,
                    "srid": m.srid,
                    "is_primary_key": m.is_primary_key,
                    "skip": m.skip,
                }
                for m in mappings
            ],
            "suggested_match_columns": suggested_match,
            "existing_columns": existing_columns,
        }

    except Exception as e:
        logger.error(f"[CRASH] Errore auto-mapping: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# ENDPOINT: CHECK CONFLITTI

@router.post("/check-conflicts")
async def check_conflicts(request: ConflictCheckRequest, current_user: CurrentUser = Depends(require_not_esecutore)):
    """POST /api/carica/check-conflicts

    Verifica i conflitti prima dell'inserimento in modalità APPEND.
    Il frontend invia un campione di valori delle colonne match e
    riceve il report dei conflitti trovati.
    """
    try:
        connection = _get_connection(request.connection_id)
        db_type = connection.get("type", "postgres")
        engine = _create_engine(connection)

        from app.core.carica.conflict_checker import ConflictChecker
        import pandas as pd

        checker = ConflictChecker(engine, db_type)

        if request.sample_values:
            # Check su campione esplicito inviato dal frontend
            df = pd.DataFrame(request.sample_values)
        elif request.source_connection_id and request.source_table:
            # Query diretta al DB sorgente — non richiede esecuzione previa della pipeline
            src_conn = _get_connection(request.source_connection_id)
            src_engine = _create_engine(src_conn)
            import re
            _ident_re = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
            src_schema = request.source_schema or "public"
            src_table = request.source_table
            src_cols = request.source_match_columns or request.match_columns
            # Validazione identificatori per prevenire SQL injection
            for ident in [src_schema, src_table] + list(src_cols):
                if not _ident_re.match(ident):
                    raise HTTPException(400, f"Nome identificatore non valido: {ident}")
            from sqlalchemy.sql import quoted_name
            q_schema = quoted_name(src_schema, quote=True)
            q_table = quoted_name(src_table, quote=True)
            quoted_cols = ", ".join(quoted_name(c, quote=True) for c in src_cols)
            src_query = f"SELECT DISTINCT {quoted_cols} FROM {q_schema}.{q_table}"
            logger.info("[C07-SRC] Query sorgente diretta eseguita")
            with src_engine.connect() as src_conn_obj:
                result_proxy = src_conn_obj.execute(text(src_query))
                rows = result_proxy.fetchall()
                col_names = list(result_proxy.keys())
            df = pd.DataFrame(rows, columns=col_names)
            # Rinomina colonne sorgente → nomi DB destinazione (match_columns)
            if request.source_match_columns and request.source_match_columns != request.match_columns:
                rename_map = dict(zip(request.source_match_columns, request.match_columns))
                df = df.rename(columns=rename_map)
            logger.info(f"[C07-SRC] DataFrame sorgente: {len(df)} righe distinte")
        elif request.project_id:
            # Authz: verifica che l'utente abbia accesso al progetto prima
            # di leggerne il result blob. Senza questo check, chiunque con
            # accesso a una connection di destinazione potrebbe leggere
            # l'output di un progetto di un altro gruppo conoscendone l'id.
            from app.shared.authz_helpers import assert_project_visible
            assert_project_visible(request.project_id, current_user)

            # Defense-in-depth core-level: il SourceLoader propaga
            # expected_group_id al resolver, che applica il check
            # cross-tenant fail-closed anche se l'ACL HTTP a monte
            # venisse bypassata da chiamate interne future.
            from app.core.artifacts import (
                LatestProjectArtifactRef,
                SourceLoader,
                SourceLoadingError,
                SourceRef,
            )
            try:
                df = SourceLoader().load(
                    SourceRef(project=LatestProjectArtifactRef(project_id=request.project_id)),
                    expected_group_id=current_user.group_id,
                )
            except SourceLoadingError as e:
                raise HTTPException(status_code=403, detail=str(e))

            if df is not None:
                # Usa df_match_columns (alias) per leggere il DataFrame
                logger.info(f"[C07] match_columns={request.match_columns} df_match_columns={request.df_match_columns}")
                df_cols = request.df_match_columns or request.match_columns
                available_match = [c for c in df_cols if c in df.columns]
                if available_match:
                    # Rinomina le colonne alias → nomi DB per il checker
                    col_rename = {}
                    if request.df_match_columns:
                        for db_col, df_col in zip(request.match_columns, request.df_match_columns):
                            if df_col != db_col and df_col in df.columns:
                                col_rename[df_col] = db_col
                    df = df[available_match]
                    if col_rename:
                        df = df.rename(columns=col_rename)
                else:
                    df = pd.DataFrame(columns=request.match_columns)
            else:
                return {
                    "has_conflicts": False,
                    "total_conflicts": 0,
                    "internal_duplicates": 0,
                    "db_conflicts": 0,
                    "details": [],
                    "message": "Nessun output disponibile. Esegui prima la pipeline.",
                }
        else:
            return {
                "has_conflicts": False,
                "total_conflicts": 0,
                "internal_duplicates": 0,
                "db_conflicts": 0,
                "details": [],
                "message": "Nessun dato da verificare. Esegui prima la pipeline.",
            }

        report = checker.check(
            df,
            request.schema_name,
            request.table_name,
            request.match_columns,
        )

        return report.to_dict()

    except Exception as e:
        logger.error(f"[CRASH] Errore check conflitti: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


# HELPER

def _get_connection(connection_id: str) -> Dict[str, Any]:
    """Recupera la configurazione di connessione come dizionario."""
    if _connection_service:
        conn = _connection_service.get_connection(connection_id)
        if conn:
            # ConnectionService restituisce sempre un DatabaseConnection (Pydantic v2)
            return conn.model_dump()
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.CONNECTION_NOT_FOUND,
                "message": f"Connessione '{connection_id}' non trovata",
            },
        )
    raise_typed_http(
        error_codes.SERVICE_NOT_INITIALIZED,
        status_code=503,
        message="ConnectionService non inizializzato",
    )


def _create_engine(connection: Dict[str, Any]):
    """Crea engine SQLAlchemy dalla connessione."""
    try:
        from app.db.query_executor import DefaultEngineFactory
        factory = DefaultEngineFactory()
        return factory.create_engine(_dict_to_conn_obj(connection))
    except ImportError:
        from sqlalchemy import create_engine
        db_type = connection.get("type", "postgres")
        host = connection.get("host", "localhost")
        port = connection.get("port", 5432)
        database = connection.get("database", "")
        username = connection.get("username", "")
        password = connection.get("password", "")

        from urllib.parse import quote_plus as _qp
        if db_type == "postgres":
            url = f"postgresql://{_qp(username)}:{_qp(password)}@{host}:{port}/{database}"
        elif db_type in ("mysql", "mariadb"):
            url = f"mysql+pymysql://{_qp(username)}:{_qp(password)}@{host}:{port}/{database}"
        elif db_type == "mssql":
            url = (
                f"mssql+pyodbc://{_qp(username)}:{_qp(password)}@{host}:{port}/{database}"
                f"?driver=ODBC+Driver+18+for+SQL+Server"
                f"&TrustServerCertificate=yes&Encrypt=Optional"
            )
        elif db_type == "oracle":
            url = f"oracle+oracledb://{_qp(username)}:{_qp(password)}@{host}:{port}/{database}"
        else:
            raise ValueError(f"Database type '{db_type}' non supportato")

        return create_engine(url)
