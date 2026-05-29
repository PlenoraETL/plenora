"""
Router API per il modulo CARICA.

Owns the HTTP boundary and delegates business logic to services or helpers.
"""

from urllib.parse import quote_plus as _qp

from app.utils.logger import get_logger
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.core.auth.dependencies import require_not_esecutore
from app.shared.contracts.auth import CurrentUser
from app.core.auth.ownership import assert_resource_visible
from app.core.carica.executor import CaricaExecutor, _dict_to_conn_obj
from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.column_mapper import ColumnMapper
from app.core.carica.conflict_checker import ConflictChecker
from app.core.carica import conflict_request_loader as _conflict_request_loader
from app.core.carica.ddl_generator import DDLGenerator
from app.core.carica.models import WriteConfig
from app.core.artifacts import (
    LatestProjectArtifactRef,
    SourceLoader,
    SourceRef,
)
from app.core.connections import errors as connection_errors
from app.core.connections.service import ConnectionService as _CS
from app.core.resources.column_types import metadata_to_dict
from app.core.query_execution.query_executor import DefaultEngineFactory
from app.shared import error_codes
from app.shared.contracts.payloads import JsonObject, JsonRows, StringRows
from app.core.auth.resource_access import assert_project_visible
from app.shared.error_envelope import raise_typed_http
from app.shared.security import tls_policy as tls_policy_module


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
    # il DataFrame vero non è serializzabile via API
    source_columns: StringRows = []


class ValidateRequest(BaseModel):
    """Richiesta per validazione configurazione."""
    connection_id: str
    node_data: JsonObject
    source_columns: StringRows = []
    total_rows: int = 0


class ConflictCheckRequest(BaseModel):
    """Richiesta per check conflitti."""
    connection_id: str
    schema_name: str
    table_name: str
    match_columns: List[str]
    project_id: Optional[str] = None
    df_match_columns: Optional[List[str]] = None
    sample_values: JsonRows = []
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
    columns: StringRows
    table_name: str


def _assert_connection_visible(connection_id: str, current_user: CurrentUser) -> None:
    connection = _CS().get_connection(connection_id)
    assert_resource_visible(connection, "connections", current_user, "Connessione")


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
    # owned dal suo gruppo o shared). Senza questo check un utente di
    # gruppoA potrebbe enumerare schemi/tabelle del DB di gruppoB
    _assert_connection_visible(connection_id, current_user)
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
    _assert_connection_visible(connection_id, current_user)
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


# NOTE: l'endpoint `GET /columns/{connection_id}/{schema_name}/{table_name}`
# e' stato rimosso il 2026-05-14. Il FE non lo chiamava piu` dal commit
# f71d0a0 (dead fetch `loadDestColumns` rimossa); nessun test BE lo
# referenziava. La funzionalita` "leggi colonne tabella DB
# destinazione" si fa via `POST /carica/auto-mapping` che ritorna
# `existing_columns` come parte del payload mapping.


# ENDPOINT: AUTO-MAPPING

@router.post("/auto-mapping")
async def auto_mapping(request: AutoMappingRequest, current_user: CurrentUser = Depends(require_not_esecutore)):
    """POST /api/carica/auto-mapping

    Genera automaticamente il mapping colonne sorgente → destinazione.
    Il frontend invia la lista colonne sorgente (dal nodo Result) e
    riceve il mapping proposto che l'utente può modificare.
    """
    _assert_connection_visible(request.connection_id, current_user)
    try:
        from app.core.dataframes.schema import empty_dataframe_from_column_metadata

        # Ricostruisci un DataFrame vuoto con le colonne sorgente
        # per il mapping servono solo i nomi e i tipi). La dispatch
        # canvas-type -> pandas dtype resta confinata in app.core.dataframes.
        df = empty_dataframe_from_column_metadata(request.source_columns)

        # Canvas types (dict form del tipo semantico) per rilevare geometrie
        # `source_columns` arriva gia' come list[{"name","type"}]:
        # l'adapter fa la sola conversione di forma.
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
    _assert_connection_visible(request.connection_id, current_user)
    try:
        connection = _get_connection(request.connection_id)
        db_type = connection.get("type", "postgres")
        engine = _create_engine(connection)

        checker = ConflictChecker(engine, db_type)

        conflict_input = _conflict_request_loader.load_conflict_input(
            request,
            current_user,
            get_connection=_get_connection,
            create_engine=_create_engine,
            project_visible_checker=assert_project_visible,
            source_loader_cls=SourceLoader,
            source_ref_cls=SourceRef,
            latest_project_artifact_ref_cls=LatestProjectArtifactRef,
            logger=logger,
        )
        if conflict_input.early_response is not None:
            return conflict_input.early_response
        df = conflict_input.dataframe

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

def _get_connection(connection_id: str) -> JsonObject:
    """Recupera la configurazione di connessione come dizionario."""
    if _connection_service:
        conn = _connection_service.get_connection(connection_id)
        if conn:
            # ConnectionService restituisce sempre un DatabaseConnection (Pydantic v2
            return conn.model_dump()
        raise HTTPException(
            status_code=404,
            detail={
                "error": connection_errors.CONNECTION_NOT_FOUND,
                "message": f"Connessione '{connection_id}' non trovata",
            },
        )
    raise_typed_http(
        error_codes.SERVICE_NOT_INITIALIZED,
        status_code=503,
        message="ConnectionService non inizializzato",
    )


def _create_engine(connection: JsonObject):
    """Crea engine SQLAlchemy dalla connessione."""
    try:
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

        if db_type == "postgres":
            url = f"postgresql://{_qp(username)}:{_qp(password)}@{host}:{port}/{database}"
        elif db_type in ("mysql", "mariadb"):
            url = f"mysql+pymysql://{_qp(username)}:{_qp(password)}@{host}:{port}/{database}"
        elif db_type == "mssql":
            # H-2 (2026-05-26): TrustServerCertificate gated da tls_bypass_allowed()
            trust_cert = "yes" if tls_policy_module.tls_bypass_allowed() else "no"
            url = (
                f"mssql+pyodbc://{_qp(username)}:{_qp(password)}@{host}:{port}/{database}"
                f"?driver=ODBC+Driver+18+for+SQL+Server"
                f"&TrustServerCertificate={trust_cert}&Encrypt=Optional"
            )
        elif db_type == "oracle":
            url = f"oracle+oracledb://{_qp(username)}:{_qp(password)}@{host}:{port}/{database}"
        else:
            raise ValueError(f"Database type '{db_type}' non supportato")

        return create_engine(url)
