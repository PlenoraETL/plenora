"""
Entry point per l'esecuzione del modulo CARICA.

Architettura
============
Questo modulo è il punto di ingresso per il modulo CARICA. Riceve il
DataFrame (output del nodo Result) e la configurazione dal nodo canvas,
e orchestra il processo di scrittura:

1. Parsing configurazione dal nodo canvas
2. Creazione engine SQLAlchemy (riusa DefaultEngineFactory di ESTRAI)
3. Auto-mapping colonne se non configurate dall'utente
4. Validazione pre-scrittura
5. Creazione del DatabaseWriter e esecuzione
6. Salvataggio audit record

Questo file è intenzionalmente snello: la logica complessa è nei moduli
specializzati (database_writer per la scrittura, ddl_generator per il DDL,
column_mapper per il mapping, conflict_checker per i conflitti).

Chiamato da:
- core/manipola/executor.py quando il nodo Output ha un nodo CARICA collegato
- workers/tasks/carica_task.py per l'esecuzione asincrona via Celery

Uso:
    executor = CaricaExecutor(connection_service)
    result = executor.execute(df, canvas_node_data, project_id)
"""

import json
from app.utils.logger import get_logger
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from app.core.carica.models import (
    AuditRecord,
    WriteConfig,
    WriteResult,
    WriteStatus,
)
from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.column_mapper import ColumnMapper


def _short_err(e: Exception, max_len: int = 120) -> str:
    """Tronca il messaggio di eccezione alla prima riga significativa."""
    msg = str(e).strip()
    first_line = msg.split("\n")[0].strip()
    return first_line[:max_len] if len(first_line) > max_len else first_line



logger = get_logger("app.core.carica.executor")


def _dict_to_conn_obj(connection: Dict[str, Any]):
    """
    Converte un dict di connessione in un oggetto con attributi.

    DefaultEngineFactory si aspetta un oggetto con .host, .port, ecc.
    invece di un dict. Questa utility evita la ridefinizione inline
    di ConnObj in più posti del modulo carica.
    """
    class _ConnObj:
        pass
    obj = _ConnObj()
    for key, val in connection.items():
        setattr(obj, key, val)
    return obj


class CaricaExecutor:
    """Executor del modulo CARICA: orchestra la scrittura su database.

    Attributes:
        connection_service: servizio per recuperare le connessioni database
        engine_factory: factory per creare engine SQLAlchemy (da query_executor)
        audit_dir: directory per i log di audit
    """

    def __init__(
        self,
        connection_service=None,
        engine_factory=None,
        audit_dir: str = "data/logs/carica",
    ):
        """
        Args:
            connection_service: istanza ConnectionService per recuperare connessioni
            engine_factory: DefaultEngineFactory per creare engine SQLAlchemy.
                           Se None, usa quello di default da query_executor.
            audit_dir: directory per salvare i log di audit.
        """
        self.connection_service = connection_service
        self.engine_factory = engine_factory
        self.audit_dir = audit_dir

    def execute(
        self,
        df: pd.DataFrame,
        node_data: Dict[str, Any],
        project_id: str = "",
        progress_callback: Optional[Callable] = None,
        triggered_by: str = "manual",
    ) -> WriteResult:
        """Esegue il caricamento su database.

        Args:
            df: DataFrame da scrivere (output del nodo Result)
            node_data: dati del nodo CARICA dal canvas
            project_id: ID del progetto (per audit)
            progress_callback: callback per aggiornare il progresso
            triggered_by: "manual" o "cron"

        Returns:
            WriteResult con esito dell'operazione.
        """
        logger.info(f"Avvio CARICA: {project_id}", op="execute", project_id=project_id)

        # 1. Parse configurazione dal nodo canvas
        try:
            config = WriteConfig.from_node_data(node_data)
        except Exception as e:
            logger.error(f"[CRASH] Errore parsing configurazione: {e}", op="execute", project_id=project_id, exc_info=True)
            return WriteResult(
                status=WriteStatus.VALIDATION_ERROR,
                error_message=f"Configurazione nodo CARICA non valida: {_short_err(e)}",
                total_rows=len(df),
            )

        # 2. Recupera connessione database
        try:
            connection = self._get_connection(config.connection_id)
            db_type = connection.get("type", "postgres")
        except Exception as e:
            logger.error(f"[CRASH] Errore recupero connessione: {e}", op="execute", project_id=project_id, exc_info=True)
            return WriteResult(
                status=WriteStatus.CONNECTION_ERROR,
                error_message=f"Connessione '{config.connection_id}' non trovata: {_short_err(e)}",
                total_rows=len(df),
            )

        # 3. Crea engine SQLAlchemy
        try:
            engine = self._create_engine(connection)
        except Exception as e:
            logger.error(f"[CRASH] Errore creazione engine: {e}", op="execute", project_id=project_id, exc_info=True)
            return WriteResult(
                status=WriteStatus.CONNECTION_ERROR,
                error_message=f"Connessione al database fallita: {_short_err(e)}",
                total_rows=len(df),
            )

        # 4. Auto-mapping colonne se non configurate
        if not config.column_mappings:
            mapper = ColumnMapper(db_type)
            canvas_types = node_data.get('canvas_types', {})
            config.column_mappings = mapper.auto_map(
                df,
                canvas_types=canvas_types,
                srid=config.geometry_srid,
            )
            # B-BE-59: correggi dest_column se il DataFrame usa alias.
            # availableColumns porta {name: "id", alias: "id2"} -> alias→originale.
            available_cols = node_data.get('availableColumns', [])
            if available_cols:
                alias_to_original = {}
                for c in available_cols:
                    if isinstance(c, dict) and c.get('alias') and c.get('name'):
                        alias_to_original[c['alias']] = c['name']
                if alias_to_original:
                    for cm in config.column_mappings:
                        if cm.dest_column in alias_to_original:
                            logger.info(
                                f"B-BE-59: dest_column '{cm.dest_column}' -> '{alias_to_original[cm.dest_column]}' (alias corretto)"
                            )
                            cm.dest_column = alias_to_original[cm.dest_column]

        # 5. Crea writer e valida
        writer = DatabaseWriter(
            config=config,
            engine=engine,
            db_type=db_type,
            progress_callback=progress_callback,
        )

        is_valid, error_msg = writer.validate()
        if not is_valid:
            logger.error(f"[GESTITO] Validazione fallita: {error_msg}", op="validate", project_id=project_id)
            return WriteResult(
                status=WriteStatus.VALIDATION_ERROR,
                error_message=error_msg,
                total_rows=len(df),
            )

        # 6. Esegui scrittura
        result = writer.write(df)

        # 7. Salva audit
        self._save_audit(result, config, project_id, triggered_by)

        logger.info(
            f"CARICA completato: {result.status.value}, "
            f"{result.rows_written}/{result.total_rows} righe scritte",
            op="execute", project_id=project_id
        )

        return result

    def validate_config(
        self,
        df: pd.DataFrame,
        node_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Valida la configurazione senza eseguire la scrittura.

        Usato dall'endpoint API per validazione pre-esecuzione.

        Returns:
            Dict con is_valid, errors, warnings, table_exists, column_count.
        """
        errors = []
        warnings = []

        # Parse config
        try:
            config = WriteConfig.from_node_data(node_data)
        except Exception as e:
            return {
                "is_valid": False,
                "errors": [f"Configurazione non valida: {str(e)}"],
                "warnings": [],
            }

        # Verifica connessione
        try:
            connection = self._get_connection(config.connection_id)
            engine = self._create_engine(connection)
            db_type = connection.get("type", "postgres")
        except Exception as e:
            return {
                "is_valid": False,
                "errors": [f"Connessione non disponibile: {str(e)}"],
                "warnings": [],
            }

        # Verifica tabella
        writer = DatabaseWriter(config=config, engine=engine, db_type=db_type)
        table_exists = writer.table_exists()

        # Validazione mapping
        if config.column_mappings:
            mapper = ColumnMapper(db_type)
            valid, mapping_errors = mapper.validate_mapping(
                config.column_mappings, df
            )
            errors.extend(mapping_errors)

        # Check specifici per modalità
        is_valid, mode_error = writer.validate()
        if not is_valid:
            errors.append(mode_error)

        # Warnings
        if config.batch_size > 10000:
            warnings.append(
                f"Batch size elevato ({config.batch_size}). "
                f"Potrebbe causare problemi di memoria."
            )
        if len(df) > 100000:
            warnings.append(
                f"Dataset grande ({len(df)} righe). "
                f"Consigliato l'uso di task asincrono."
            )

        return {
            "is_valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "table_exists": table_exists,
            "column_count": len(config.column_mappings),
            "total_rows": len(df),
        }

    # ========================================================================
    # HELPER INTERNI
    # ========================================================================

    def _get_connection(self, connection_id: str) -> Dict[str, Any]:
        """Recupera la configurazione di connessione."""
        if self.connection_service:
            conn = self.connection_service.get_connection(connection_id)
            if conn:
                if hasattr(conn, 'dict'):
                    return conn.dict()
                if hasattr(conn, '__dict__'):
                    return conn.__dict__
                return conn
            raise ValueError(f"Connessione '{connection_id}' non trovata")

        # Fallback: leggi dal DB SQLite con decriptazione password
        import app.db.db_service as db_svc
        from app.services.connection_service import _decrypt_password
        row = db_svc.fetchone("SELECT * FROM connections WHERE id = ?", (connection_id,))
        if row:
            r = dict(row)
            return {
                "id": r["id"], "name": r["name"], "type": r["db_type"],
                "host": r.get("host", ""), "port": r.get("port"),
                "database": r.get("database", ""), "db_schema": r.get("db_schema", ""),
                "username": r.get("username", ""),
                "password": _decrypt_password(r.get("password", "")),
            }

        raise ValueError(f"Connessione '{connection_id}' non trovata")

    def _create_engine(self, connection: Dict[str, Any]):
        """Crea un engine SQLAlchemy dalla connessione."""
        if self.engine_factory:
            return self.engine_factory.create_engine(connection)

        # Fallback: usa DefaultEngineFactory da query_executor
        try:
            from app.db.query_executor import DefaultEngineFactory
            factory = DefaultEngineFactory()
            return factory.create_engine(_dict_to_conn_obj(connection))
        except ImportError:
            # Costruisci connection string manualmente
            return self._build_engine_manual(connection)

    def _build_engine_manual(self, conn: Dict[str, Any]):
        """Costruisce engine SQLAlchemy manualmente."""
        from sqlalchemy import create_engine

        db_type = conn.get("type", "postgres")
        host = conn.get("host", "localhost")
        port = conn.get("port", 5432)
        database = conn.get("database", "")
        username = conn.get("username", "")
        password = conn.get("password", "")

        if db_type == "postgres":
            # gssencmode=disable evita Kerberos/GSSAPI su Windows
            url = f"postgresql://{username}:{password}@{host}:{port}/{database}?gssencmode=disable"
        elif db_type == "mysql" or db_type == "mariadb":
            url = f"mysql+pymysql://{username}:{password}@{host}:{port}/{database}"
        elif db_type == "mssql":
            # Encrypt=Optional per supportare server senza TLS valido (sviluppo, on-prem legacy)
            url = (
                f"mssql+pyodbc://{username}:{password}@{host}:{port}/{database}"
                f"?driver=ODBC+Driver+18+for+SQL+Server"
                f"&TrustServerCertificate=yes&Encrypt=Optional"
            )
        elif db_type == "oracle":
            url = f"oracle+oracledb://{username}:{password}@{host}:{port}/{database}"
        else:
            raise ValueError(f"Database type '{db_type}' non supportato")

        return create_engine(url)

    def _save_audit(
        self,
        result: WriteResult,
        config: WriteConfig,
        project_id: str,
        triggered_by: str,
    ):
        """Salva il record di audit su log.db."""
        try:
            audit = AuditRecord.from_write_result(
                result, config, project_id, triggered_by
            )
            audit_dict = audit.to_dict()
            from app.utils.logger import get_logger as _get_logger
            _audit_logger = _get_logger("app.core.carica.audit")
            _audit_logger.info(
                f"CARICA audit: {config.table_name} -> {result.status.value}",
                op="carica_audit",
                project_id=project_id,
                details=json.dumps(audit_dict, default=str)
            )
        except Exception as e:
            logger.warning(f"Errore salvataggio audit: {e}")