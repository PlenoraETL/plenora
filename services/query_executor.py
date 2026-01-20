"""
Service per eseguire query SQL sui database.
Gestisce l'esecuzione per diversi tipi di database.

REFACTORED: Usa utils/dataframe_utils.py e utils/geo_utils.py
per eliminare codice duplicato nel salvataggio output.

SICUREZZA: Supporta parametri preparati per prevenire SQL injection.
"""

import csv
import json
import time
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from models import DatabaseConnection
from config import settings
from utils.logger import get_logger

# Import nuove utility centralizzate
from utils.dataframe_utils import save_dataframe

logger = get_logger(__name__)


class QueryExecutor:
    """Esegue query SQL e salva risultati."""
    
    def __init__(self):
        self.RESULTS_DIR = settings.RESULTS_DIR
    
    def execute_query(
        self,
        connection: DatabaseConnection,
        sql: str,
        project_id: str,
        output_filename: str,
        output_format: str = "csv",
        params: Optional[Dict[str, Any]] = None,
        geom_column: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Esegue query e salva risultati.
        
        Args:
            connection: DatabaseConnection da usare
            sql: Query SQL da eseguire (con placeholder :param_name)
            project_id: ID del progetto
            output_filename: Nome file output (senza estensione)
            output_format: Formato output (csv, json, xlsx, geojson, gpkg, shp)
            params: Dizionario parametri per query preparate
            geom_column: Nome colonna geometria per export geospaziale (opzionale)
            
        Returns:
            Dict con risultato esecuzione
        """
        timings = {}
        total_start = time.time()
        
        try:
            project_results_dir = self.RESULTS_DIR / project_id
            project_results_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Esecuzione query per progetto {project_id}")
            print(f"=== SQL QUERY ===\n{sql}\n=================")
            
            # Esegui query in base al tipo DB (include connessione + esecuzione + fetch)
            t_query = time.time()
            rows, columns = self._execute_by_db_type(connection, sql, params)
            timings['query_total'] = time.time() - t_query
            logger.info(f"⏱️ Query totale (connessione + esecuzione + fetch): {timings['query_total']:.2f}s")
            
            # Converti in DataFrame
            t_df = time.time()
            df = pd.DataFrame(rows, columns=columns)
            timings['dataframe'] = time.time() - t_df
            logger.info(f"⏱️ Creazione DataFrame ({len(rows)} righe): {timings['dataframe']:.2f}s")
            
            # Salva usando utility centralizzata
            t_save = time.time()
            output_path = project_results_dir / output_filename
            saved_path = save_dataframe(
                df=df,
                output_path=output_path,
                format=output_format,
                geom_column=geom_column
            )
            timings['save'] = time.time() - t_save
            logger.info(f"⏱️ Salvataggio file ({output_format}): {timings['save']:.2f}s")
            
            timings['total'] = time.time() - total_start
            logger.info(f"⏱️ TOTALE: {timings['total']:.2f}s")
            
            return {
                "success": True,
                "rows_count": len(rows),
                "columns": columns,
                "output_path": str(saved_path),
                "project_id": project_id,
                "message": f"Query eseguita. File: {saved_path.name}",
                "timings": timings
            }

        except Exception as e:
            logger.error(f"Errore esecuzione query: {str(e)}")
            return {
                "success": False,
                "message": f"Errore esecuzione: {str(e)}"
            }
    
    def _execute_by_db_type(
        self, 
        connection: DatabaseConnection, 
        sql: str, 
        params: Optional[Dict[str, Any]]
    ) -> Tuple[List[tuple], List[str]]:
        """Dispatcher per esecuzione query per tipo database."""
        
        dispatch = {
            "postgres": self._execute_postgres,
            "mssql": self._execute_mssql,
            "mysql": self._execute_mysql,
            "oracle": self._execute_oracle,
            "sqlite": self._execute_sqlite,
        }
        
        executor = dispatch.get(connection.type)
        if not executor:
            raise ValueError(f"Tipo database '{connection.type}' non supportato")
        
        return executor(connection, sql, params)
    
    # =========================================================================
    # PLACEHOLDER CONVERSION
    # =========================================================================
    
    def _replace_placeholders(self, sql: str, style: str) -> str:
        """
        Converte i placeholder :name nel formato del driver specifico.
        
        Args:
            sql: Query con placeholder :name
            style: 'pyformat' | 'named' | 'qmark'
            
        NOTA: Ignora :: che PostgreSQL usa per i cast (es: col::numeric)
        """
        import re
        
        if style == 'pyformat':
            # :name -> %(name)s
            # Usa negative lookbehind per ignorare :: (cast PostgreSQL)
            return re.sub(r'(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)', r'%(\1)s', sql)
        elif style == 'qmark':
            # Per MSSQL: :name -> ?
            # Usa negative lookbehind per ignorare ::
            return re.sub(r'(?<!:):[a-zA-Z_][a-zA-Z0-9_]*', '?', sql)
        
        # 'named' - già nel formato corretto
        return sql
    
    def _get_ordered_params(self, sql: str, params: Dict[str, Any]) -> List:
        """Estrae parametri in ordine per driver con placeholder ?."""
        import re
        param_names = re.findall(r':([a-zA-Z_][a-zA-Z0-9_]*)', sql)
        return [params.get(name) for name in param_names]
    
    # =========================================================================
    # DATABASE EXECUTORS
    # =========================================================================
    
    def _execute_postgres(
        self, 
        conn: DatabaseConnection, 
        sql: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[tuple], List[str]]:
        """Esegui query su PostgreSQL con parametri sicuri."""
        import psycopg2
        
        connection = psycopg2.connect(
            host=conn.host,
            port=conn.port,
            database=conn.database,
            user=conn.username,
            password=conn.password,
            connect_timeout=60
        )
        
        try:
            cursor = connection.cursor()
            converted_sql = self._replace_placeholders(sql, 'pyformat')
            cursor.execute(converted_sql, params)
            
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            
            return rows, columns
        finally:
            cursor.close()
            connection.close()
    
    def _execute_mssql(
        self, 
        conn: DatabaseConnection, 
        sql: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[tuple], List[str]]:
        """Esegui query su MS SQL Server con parametri sicuri."""
        import pyodbc
        
        # Prova prima Driver 18, poi Driver 17
        drivers = ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"]
        connection = None
        
        for driver in drivers:
            try:
                conn_string = (
                    f"DRIVER={{{driver}}};"
                    f"SERVER={conn.host},{conn.port};"
                    f"DATABASE={conn.database};"
                    f"UID={conn.username};"
                    f"PWD={conn.password};"
                    f"TrustServerCertificate=yes"
                )
                connection = pyodbc.connect(conn_string, timeout=60)
                break
            except pyodbc.InterfaceError:
                continue
        
        if connection is None:
            raise ValueError("Nessun driver ODBC per SQL Server trovato. Installa ODBC Driver 17 o 18.")
        
        try:
            cursor = connection.cursor()
            
            if params:
                converted_sql = self._replace_placeholders(sql, 'qmark')
                param_list = self._get_ordered_params(sql, params)
                cursor.execute(converted_sql, param_list)
            else:
                cursor.execute(sql)
            
            rows = [tuple(row) for row in cursor.fetchall()]  # Converti Row in tuple
            columns = [desc[0] for desc in cursor.description]
            
            return rows, columns
        finally:
            cursor.close()
            connection.close()
    
    def _execute_mysql(
        self, 
        conn: DatabaseConnection, 
        sql: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[tuple], List[str]]:
        """Esegui query su MySQL/MariaDB con parametri sicuri."""
        import pymysql
        
        # Timer connessione
        t_conn = time.time()
        connection = pymysql.connect(
            host=conn.host,
            port=conn.port,
            database=conn.database,
            user=conn.username,
            password=conn.password,
            connect_timeout=60
        )
        logger.info(f"⏱️ MySQL - Connessione: {time.time() - t_conn:.2f}s")
        
        try:
            cursor = connection.cursor()
            converted_sql = self._replace_placeholders(sql, 'pyformat')
            
            # Timer esecuzione
            t_exec = time.time()
            cursor.execute(converted_sql, params)
            logger.info(f"⏱️ MySQL - Esecuzione SQL: {time.time() - t_exec:.2f}s")
            
            # Timer fetch
            t_fetch = time.time()
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            logger.info(f"⏱️ MySQL - Fetch {len(rows)} righe: {time.time() - t_fetch:.2f}s")
            
            return rows, columns
        finally:
            cursor.close()
            connection.close()
    
    def _execute_oracle(
        self, 
        conn: DatabaseConnection, 
        sql: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[tuple], List[str]]:
        """Esegui query su Oracle con parametri sicuri."""
        connection = None
        
        # Prova prima oracledb (nuovo)
        try:
            import oracledb
            dsn = f"{conn.host}:{conn.port}/{conn.database}"
            logger.info(f"Oracle: tentativo connessione con oracledb a {dsn}")
            connection = oracledb.connect(
                user=conn.username,
                password=conn.password,
                dsn=dsn
            )
            
            # Handler per convertire LOB in stringhe automaticamente
            def output_type_handler(cursor, name, default_type, size, precision, scale):
                if default_type == oracledb.DB_TYPE_CLOB:
                    return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
                if default_type == oracledb.DB_TYPE_BLOB:
                    return cursor.var(oracledb.DB_TYPE_LONG_RAW, arraysize=cursor.arraysize)
            
            connection.outputtypehandler = output_type_handler
            
        except ImportError:
            # Fallback a cx_Oracle (legacy)
            try:
                import cx_Oracle
                dsn = cx_Oracle.makedsn(conn.host, conn.port, service_name=conn.database)
                logger.info(f"Oracle: tentativo connessione con cx_Oracle")
                connection = cx_Oracle.connect(
                    user=conn.username,
                    password=conn.password,
                    dsn=dsn
                )
                
                # Handler per cx_Oracle
                def output_type_handler(cursor, name, default_type, size, precision, scale):
                    if default_type == cx_Oracle.CLOB:
                        return cursor.var(cx_Oracle.LONG_STRING, arraysize=cursor.arraysize)
                    if default_type == cx_Oracle.BLOB:
                        return cursor.var(cx_Oracle.LONG_BINARY, arraysize=cursor.arraysize)
                
                connection.outputtypehandler = output_type_handler
                
            except ImportError:
                raise ValueError("Driver Oracle non installato. Installa con: pip install oracledb")
        except Exception as e:
            logger.error(f"Oracle: errore connessione oracledb: {e}")
            raise
        
        if connection is None:
            raise ValueError("Impossibile connettersi a Oracle")
        
        cursor = None
        try:
            cursor = connection.cursor()
            # Aumenta il prefetch per performance
            cursor.prefetchrows = 10000
            cursor.arraysize = 10000
            
            logger.info(f"Oracle: esecuzione query")
            cursor.execute(sql, params or {})
            
            columns = [desc[0] for desc in cursor.description]
            rows = [tuple(row) for row in cursor.fetchall()]
            
            logger.info(f"Oracle: recuperate {len(rows)} righe")
            return rows, columns
            
        except Exception as e:
            logger.error(f"Oracle: errore esecuzione: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
    
    def _execute_sqlite(
        self, 
        conn: DatabaseConnection, 
        sql: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[tuple], List[str]]:
        """Esegui query su SQLite con parametri sicuri."""
        import sqlite3
        
        connection = sqlite3.connect(conn.database, timeout=60)
        
        try:
            cursor = connection.cursor()
            # SQLite supporta :name nativamente
            cursor.execute(sql, params or {})
            
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            
            return rows, columns
        finally:
            cursor.close()
            connection.close()