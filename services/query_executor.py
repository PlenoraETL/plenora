"""
Service per eseguire query SQL sui database
Gestisce l'esecuzione per diversi tipi di database

SICUREZZA: Supporta parametri preparati per prevenire SQL injection
"""
import csv
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from models import DatabaseConnection
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

class QueryExecutor:
    """Esegue query SQL e salva risultati"""
    
    def __init__(self):
            self.RESULTS_DIR = settings.RESULTS_DIR
    
    def execute_query(
        self,
        connection: DatabaseConnection,
        sql: str,
        project_id: str,
        output_filename: str,
        output_format: str = "csv",
        params: Optional[Dict[str, Any]] = None  # NUOVO: parametri query
    ) -> Dict[str, Any]:
        """
        Esegue query e salva risultati in una sottocartella specifica per il progetto.
        
        Args:
            connection: DatabaseConnection da usare
            sql: Query SQL da eseguire (con placeholder :param_name)
            project_id: ID del progetto per l'organizzazione dei file
            output_filename: Nome file output (senza estensione)
            output_format: Formato output (csv, json, xlsx)
            params: Dizionario parametri per query preparate (opzionale)
            
        Returns:
            Dict con risultato esecuzione
        """
        try:
            # 1. Creiamo la sottocartella specifica per il progetto se non esiste
            project_results_dir = self.RESULTS_DIR / project_id
            project_results_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Esecuzione query per progetto {project_id}")
            
            # 2. Esegui query in base al tipo DB
            if connection.type == "postgres":
                rows, columns = self._execute_postgres(connection, sql, params)
            elif connection.type == "mssql":
                rows, columns = self._execute_mssql(connection, sql, params)
            elif connection.type == "mysql":
                rows, columns = self._execute_mysql(connection, sql, params)
            elif connection.type == "sqlite":
                rows, columns = self._execute_sqlite(connection, sql, params)
            else:
                raise ValueError(f"Tipo database '{connection.type}' non supportato")
            
            # 3. Salvataggio nel path deterministico
            if output_format == "csv":
                output_path = self._save_csv(rows, columns, project_results_dir, output_filename)
            elif output_format == "json":
                output_path = self._save_json(rows, columns, project_results_dir, output_filename)
            elif output_format == "xlsx":
                output_path = self._save_xlsx(rows, columns, project_results_dir, output_filename)
            else:
                raise ValueError(f"Formato output '{output_format}' non supportato")
            
            return {
                "success": True,
                "rows_count": len(rows),
                "columns": columns,
                "output_path": str(output_path),
                "project_id": project_id,
                "message": f"Query eseguita con successo. File salvato in {project_id}/{output_filename}.{output_format}"
            }

        except Exception as e:
            logger.error(f"Errore esecuzione query: {str(e)}")
            return {
                "success": False,
                "message": f"Errore esecuzione: {str(e)}"
            }
    
    def _convert_params_for_driver(
        self, 
        params: Optional[Dict[str, Any]], 
        style: str
    ) -> Tuple[str, Any]:
        """
        Converte i parametri nel formato richiesto dal driver specifico.
        
        Ogni driver DB ha un formato diverso per i placeholder:
        - PostgreSQL (psycopg2): %(name)s
        - MySQL: %(name)s  
        - SQLite: :name
        - MSSQL (pyodbc): ?
        
        Args:
            params: Dizionario {nome: valore}
            style: 'pyformat' | 'named' | 'qmark'
            
        Returns:
            Tuple (sql_modificato, params_convertiti)
        """
        if not params:
            return None
            
        if style == 'pyformat':
            # PostgreSQL e MySQL: %(name)s
            return params
        elif style == 'named':
            # SQLite: :name (già nel formato corretto)
            return params
        elif style == 'qmark':
            # MSSQL: ? (richiede lista ordinata)
            # Non supportato per ora, usiamo named se possibile
            return params
        
        return params
    
    def _replace_placeholders(
        self, 
        sql: str, 
        style: str
    ) -> str:
        """
        Converte i placeholder :name nel formato del driver specifico.
        
        Args:
            sql: Query con placeholder :name
            style: 'pyformat' | 'named' | 'qmark'
            
        Returns:
            Query con placeholder convertiti
        """
        import re
        
        if style == 'pyformat':
            # :name -> %(name)s
            return re.sub(r':([a-zA-Z_][a-zA-Z0-9_]*)', r'%(\1)s', sql)
        elif style == 'named':
            # Già nel formato corretto :name
            return sql
        elif style == 'qmark':
            # Per MSSQL dovremmo convertire in ? ma è complesso
            # Per ora lasciamo :name e vediamo se pyodbc lo supporta
            return sql
        
        return sql
    
    def _execute_postgres(
        self, 
        conn: DatabaseConnection, 
        sql: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[tuple], List[str]]:
        """Esegui query su PostgreSQL con parametri sicuri"""
        import psycopg2
        
        connection = psycopg2.connect(
            host=conn.host,
            port=conn.port,
            database=conn.database,
            user=conn.username,
            password=conn.password,
            connect_timeout=30
        )
        
        try:
            cursor = connection.cursor()
            
            # Converti placeholder per psycopg2: :name -> %(name)s
            converted_sql = self._replace_placeholders(sql, 'pyformat')
            
            # Esegui con parametri
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
        """Esegui query su MS SQL Server con parametri sicuri"""
        import pyodbc
        
        conn_string = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={conn.host},{conn.port};"
            f"DATABASE={conn.database};"
            f"UID={conn.username};"
            f"PWD={conn.password}"
        )
        
        connection = pyodbc.connect(conn_string, timeout=30)
        
        try:
            cursor = connection.cursor()
            
            if params:
                # pyodbc supporta named parameters con :name
                # Ma è più affidabile usare ? con lista ordinata
                # Estraiamo i parametri nell'ordine in cui appaiono
                import re
                param_names = re.findall(r':([a-zA-Z_][a-zA-Z0-9_]*)', sql)
                
                # Sostituisci :name con ?
                converted_sql = re.sub(r':[a-zA-Z_][a-zA-Z0-9_]*', '?', sql)
                
                # Crea lista parametri nell'ordine corretto
                param_list = [params.get(name) for name in param_names]
                
                cursor.execute(converted_sql, param_list)
            else:
                cursor.execute(sql)
            
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            
            # Converti Row objects in tuple
            rows = [tuple(row) for row in rows]
            
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
        """Esegui query su MySQL con parametri sicuri"""
        import mysql.connector
        
        connection = mysql.connector.connect(
            host=conn.host,
            port=conn.port,
            database=conn.database,
            user=conn.username,
            password=conn.password,
            connection_timeout=30
        )
        
        try:
            cursor = connection.cursor()
            
            # Converti placeholder per mysql: :name -> %(name)s
            converted_sql = self._replace_placeholders(sql, 'pyformat')
            
            cursor.execute(converted_sql, params)
            
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            
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
        """Esegui query su Oracle con parametri sicuri"""
        import cx_Oracle
        
        dsn = cx_Oracle.makedsn(conn.host, conn.port, service_name=conn.database)
        connection = cx_Oracle.connect(
            user=conn.username,
            password=conn.password,
            dsn=dsn
        )
        
        try:
            cursor = connection.cursor()
            
            # Oracle supporta :name nativamente
            cursor.execute(sql, params or {})
            
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            
            return rows, columns
            
        finally:
            cursor.close()
            connection.close()
    
    def _execute_sqlite(
        self, 
        conn: DatabaseConnection, 
        sql: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[tuple], List[str]]:
        """Esegui query su SQLite con parametri sicuri"""
        import sqlite3
        
        connection = sqlite3.connect(conn.database, timeout=30)
        
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
    
    def _save_csv(
        self, 
        rows: List[tuple], 
        columns: List[str], 
        target_dir: Path, 
        filename: str
    ) -> Path:
        """Salva come CSV nella cartella del progetto"""
        output_path = target_dir / f"{filename}.csv"
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)
        return output_path
    
    def _save_json(
        self, 
        rows: List[tuple], 
        columns: List[str], 
        target_dir: Path, 
        filename: str
    ) -> Path:
        """Salva come JSON nella cartella del progetto"""
        output_path = target_dir / f"{filename}.json"
        data = []
        for row in rows:
            row_dict = {columns[i]: row[i] for i in range(len(columns))}
            data.append(row_dict)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
        return output_path
    
    def _save_xlsx(
        self, 
        rows: List[tuple], 
        columns: List[str], 
        target_dir: Path, 
        filename: str
    ) -> Path:
        """Salva come Excel nella cartella del progetto"""
        try:
            from openpyxl import Workbook
            output_path = target_dir / f"{filename}.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "Results"
            ws.append(columns)
            for row in rows:
                ws.append(row)
            wb.save(output_path)
            return output_path
        except ImportError:
            raise ImportError(
                "Libreria 'openpyxl' richiesta per esportazione Excel. "
                "Esegui: pip install openpyxl"
            )