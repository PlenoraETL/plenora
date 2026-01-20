import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import List, Optional
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

from models import (
    DatabaseConnection,
    ConnectionCreate,
    ConnectionUpdate,
    ConnectionList,
    ConnectionTestResult
)


class ConnectionService:
    """Servizio per gestione connessioni database"""
    
    def __init__(self):
            self.CONNECTIONS_DIR = settings.CONNECTIONS_DIR
    
    def _generate_connection_id(self, name: str) -> str:
        """Genera ID univoco per connessione"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        # Sanitizza nome
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_')).strip()
        safe_name = safe_name.replace(' ', '_')[:30]
        
        return f"{safe_name}_{timestamp}_{short_uuid}"
    
    def _get_connection_path(self, connection_id: str) -> Path:
        """Ottieni path file connessione"""
        return self.CONNECTIONS_DIR / f"{connection_id}.json"
    
    def create_connection(self, data: ConnectionCreate) -> DatabaseConnection:
        """
        Crea nuova connessione
        
        Args:
            data: Dati connessione
            
        Returns:
            DatabaseConnection creata
        """
        # Genera ID e timestamp
        connection_id = self._generate_connection_id(data.name)
        now = datetime.now()
        
        # Crea connessione
        connection = DatabaseConnection(
            id=connection_id,
            name=data.name,
            description=data.description,
            type=data.type,
            host=data.host,
            port=data.port,
            database=data.database,
            username=data.username,
            password=data.password,  # TODO: encrypt
            created_at=now,
            updated_at=now
        )
        
        # Salva su disco
        with open(self._get_connection_path(connection_id), "w", encoding="utf-8") as f:
            f.write(connection.model_dump_json(indent=2))
        
        return connection
    
    def list_connections(self) -> ConnectionList:
        """
        Lista tutte le connessioni
        
        Returns:
            ConnectionList con tutte le connessioni
        """
        connections = []
        
        # Scansiona file connessioni
        for conn_file in self.CONNECTIONS_DIR.glob("*.json"):
            try:
                with open(conn_file, "r", encoding="utf-8") as f:
                    connection = DatabaseConnection.model_validate_json(f.read())
                    connections.append(connection)
            except Exception as e:
                logger.error(f"Errore caricamento connessione {conn_file.name}: {e}")
                continue
        
        # Ordina per data aggiornamento (più recente prima)
        connections.sort(key=lambda c: c.updated_at, reverse=True)
        
        return ConnectionList(connections=connections)
    
    def get_connection(self, connection_id: str) -> Optional[DatabaseConnection]:
        """
        Ottieni connessione specifica
        
        Args:
            connection_id: ID connessione
            
        Returns:
            DatabaseConnection o None se non esiste
        """
        conn_path = self._get_connection_path(connection_id)
        
        if not conn_path.exists():
            return None
        
        with open(conn_path, "r", encoding="utf-8") as f:
            return DatabaseConnection.model_validate_json(f.read())
    
    def update_connection(self, connection_id: str, data: ConnectionUpdate) -> Optional[DatabaseConnection]:
        """
        Aggiorna connessione esistente
        
        Args:
            connection_id: ID connessione
            data: Dati da aggiornare
            
        Returns:
            DatabaseConnection aggiornata o None se non esiste
        """
        connection = self.get_connection(connection_id)
        
        if not connection:
            return None
        
        # Aggiorna solo i campi forniti
        update_data = data.model_dump(exclude_unset=True)
        
        for field, value in update_data.items():
            setattr(connection, field, value)
        
        # Aggiorna timestamp
        connection.updated_at = datetime.now()
        
        # Salva
        with open(self._get_connection_path(connection_id), "w", encoding="utf-8") as f:
            f.write(connection.model_dump_json(indent=2))
        
        return connection
    
    def delete_connection(self, connection_id: str) -> bool:
        """
        Elimina una connessione
        
        Args:
            connection_id: ID connessione
            
        Returns:
            True se eliminata, False se non esiste
        """
        conn_path = self._get_connection_path(connection_id)
        
        if not conn_path.exists():
            return False
        
        conn_path.unlink()
        return True
    
    def connection_exists(self, connection_id: str) -> bool:
        """Verifica se connessione esiste"""
        return self._get_connection_path(connection_id).exists()
    
    def test_connection(self, connection_id: str) -> ConnectionTestResult:
        """
        Testa una connessione database
        
        Args:
            connection_id: ID connessione da testare
            
        Returns:
            ConnectionTestResult con esito test
        """
        connection = self.get_connection(connection_id)
        
        if not connection:
            return ConnectionTestResult(
                success=False,
                message=f"Connessione '{connection_id}' non trovata"
            )
        
        # Testa connessione in base al tipo
        try:
            if connection.type == "postgres":
                return self._test_postgres(connection)
            elif connection.type == "mssql":
                return self._test_mssql(connection)
            elif connection.type == "mysql":
                return self._test_mysql(connection)
            elif connection.type == "oracle":
                return self._test_oracle(connection)
            elif connection.type == "sqlite":
                return self._test_sqlite(connection)
            else:
                return ConnectionTestResult(
                    success=False,
                    message=f"Tipo database '{connection.type}' non supportato"
                )
        
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message=f"Errore test connessione: {str(e)}"
            )
    
    def _test_postgres(self, conn: DatabaseConnection) -> ConnectionTestResult:
        """Test connessione PostgreSQL"""
        try:
            import psycopg2
            
            connection = psycopg2.connect(
                host=conn.host,
                port=conn.port,
                database=conn.database,
                user=conn.username,
                password=conn.password,
                connect_timeout=5
            )
            
            cursor = connection.cursor()
            cursor.execute("SELECT version();")
            version = cursor.fetchone()[0]
            
            cursor.close()
            connection.close()
            
            return ConnectionTestResult(
                success=True,
                message="Connessione PostgreSQL riuscita",
                details={"version": version}
            )
        
        except ImportError:
            return ConnectionTestResult(
                success=False,
                message="Driver PostgreSQL (psycopg2) non installato. Installa con: pip install psycopg2-binary"
            )
        
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message=f"Errore connessione PostgreSQL: {str(e)}"
            )
    
    def _test_mssql(self, conn: DatabaseConnection) -> ConnectionTestResult:
        """Test connessione MS SQL Server"""
        try:
            import pyodbc
            
            # Prova prima con Driver 18, poi con Driver 17
            drivers = ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"]
            
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
                    
                    connection = pyodbc.connect(conn_string, timeout=5)
                    cursor = connection.cursor()
                    cursor.execute("SELECT @@VERSION;")
                    version = cursor.fetchone()[0]
                    
                    cursor.close()
                    connection.close()
                    
                    return ConnectionTestResult(
                        success=True,
                        message=f"Connessione MS SQL Server riuscita (Driver: {driver})",
                        details={"version": version[:100]}  # Truncate
                    )
                except pyodbc.InterfaceError:
                    continue  # Prova il prossimo driver
            
            return ConnectionTestResult(
                success=False,
                message="Nessun driver ODBC per SQL Server trovato. Installa ODBC Driver 17 o 18 da Microsoft."
            )
        
        except ImportError:
            return ConnectionTestResult(
                success=False,
                message="Driver MS SQL (pyodbc) non installato. Installa con: pip install pyodbc"
            )
        
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message=f"Errore connessione MS SQL Server: {str(e)}"
            )
    
    def _test_mysql(self, conn: DatabaseConnection) -> ConnectionTestResult:
        """Test connessione MySQL/MariaDB"""
        try:
            import pymysql
            
            connection = pymysql.connect(
                host=conn.host,
                port=conn.port,
                database=conn.database,
                user=conn.username,
                password=conn.password,
                connect_timeout=5
            )
            
            cursor = connection.cursor()
            cursor.execute("SELECT VERSION();")
            version = cursor.fetchone()[0]
            
            cursor.close()
            connection.close()
            
            return ConnectionTestResult(
                success=True,
                message="Connessione MySQL/MariaDB riuscita",
                details={"version": version}
            )
        
        except ImportError:
            return ConnectionTestResult(
                success=False,
                message="Driver MySQL (pymysql) non installato. Installa con: pip install pymysql"
            )
        
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message=f"Errore connessione MySQL: {str(e)}"
            )
    
    def _test_oracle(self, conn: DatabaseConnection) -> ConnectionTestResult:
        """Test connessione Oracle"""
        # Prova prima oracledb (nuovo), poi cx_Oracle (legacy)
        try:
            import oracledb
            
            connection = oracledb.connect(
                user=conn.username,
                password=conn.password,
                dsn=f"{conn.host}:{conn.port}/{conn.database}"
            )
            
            cursor = connection.cursor()
            cursor.execute("SELECT banner FROM v$version WHERE banner LIKE 'Oracle%' AND ROWNUM = 1")
            version = cursor.fetchone()[0]
            
            cursor.close()
            connection.close()
            
            return ConnectionTestResult(
                success=True,
                message="Connessione Oracle riuscita (oracledb)",
                details={"version": version}
            )
        
        except ImportError:
            # Fallback a cx_Oracle
            try:
                import cx_Oracle
                
                dsn = cx_Oracle.makedsn(conn.host, conn.port, service_name=conn.database)
                connection = cx_Oracle.connect(
                    user=conn.username,
                    password=conn.password,
                    dsn=dsn
                )
                
                cursor = connection.cursor()
                cursor.execute("SELECT banner FROM v$version WHERE banner LIKE 'Oracle%' AND ROWNUM = 1")
                version = cursor.fetchone()[0]
                
                cursor.close()
                connection.close()
                
                return ConnectionTestResult(
                    success=True,
                    message="Connessione Oracle riuscita (cx_Oracle)",
                    details={"version": version}
                )
            
            except ImportError:
                return ConnectionTestResult(
                    success=False,
                    message="Driver Oracle non installato. Installa con: pip install oracledb"
                )
            
            except Exception as e:
                return ConnectionTestResult(
                    success=False,
                    message=f"Errore connessione Oracle: {str(e)}"
                )
        
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message=f"Errore connessione Oracle: {str(e)}"
            )
    
    def _test_sqlite(self, conn: DatabaseConnection) -> ConnectionTestResult:
        """Test connessione SQLite"""
        try:
            import sqlite3
            
            connection = sqlite3.connect(conn.database, timeout=5)
            cursor = connection.cursor()
            cursor.execute("SELECT sqlite_version();")
            version = cursor.fetchone()[0]
            
            cursor.close()
            connection.close()
            
            return ConnectionTestResult(
                success=True,
                message="Connessione SQLite riuscita",
                details={"version": version}
            )
        
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message=f"Errore connessione SQLite: {str(e)}"
            )