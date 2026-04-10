"""
Servizio CRUD per connessioni database.
Storage: SQLite tramite app.db.db_service.

Le password vengono cifrate con Fernet prima di essere salvate nel DB.
La logica di cifratura è centralizzata in app.shared.crypto — qui si fa
solo l'aliasing per chiarezza nel codice del service.

Retrocompatibilità: i valori senza prefisso "enc:" sono trattati come
plaintext legacy e vengono cifrati automaticamente al primo update.
"""

import uuid
from datetime import datetime
from typing import Optional

from app.config import settings
from app.utils.logger import get_logger
from app.shared.crypto import encrypt as _encrypt_password
from app.shared.crypto import decrypt as _decrypt_password
from app.models import (
    DatabaseConnection,
    ConnectionCreate,
    ConnectionUpdate,
    ConnectionList,
    ConnectionTestResult,
)
import app.db.db_service as db

logger = get_logger(__name__)


# ─── Service ─────────────────────────────────────────────────────────────────

class ConnectionService:

    DEFAULT_GROUP = settings.DEFAULT_GROUP

    def _generate_connection_id(self, name: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_")).strip()
        safe_name = safe_name.replace(" ", "_")[:30]
        return f"{safe_name}_{timestamp}_{short_uuid}"

    def _row_to_connection(self, row: dict, mask_password: bool = False) -> Optional[DatabaseConnection]:
        if not row:
            return None
        extra = row.get("extra") or {}
        if isinstance(extra, str):
            import json
            extra = json.loads(extra)
        raw_password = row.get("password")
        if mask_password:
            display_password = "***"
        else:
            display_password = _decrypt_password(raw_password)
        return DatabaseConnection(
            id=row["id"],
            name=row["name"],
            description=extra.get("description", ""),
            type=row["db_type"],
            host=row.get("host"),
            port=row.get("port"),
            database=row.get("database"),
            db_schema=row.get("db_schema"),
            username=row.get("username"),
            password=display_password,
            group_id=row.get("group_id", ""),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def create_connection(self, data: ConnectionCreate, group_id: str = None) -> DatabaseConnection:
        group_id = group_id or self.DEFAULT_GROUP
        connection_id = self._generate_connection_id(data.name)
        extra = {"description": getattr(data, "description", "")}
        db.create_connection(
            conn_id=connection_id,
            group_id=group_id,
            name=data.name,
            db_type=data.type,
            host=data.host,
            port=data.port,
            database=data.database,
            db_schema=data.db_schema,
            username=data.username,
            password=_encrypt_password(data.password),
            extra=extra,
        )
        logger.info(f"Connessione creata: {connection_id}", op="create_connection")
        row = db.get_connection_config(connection_id)
        return self._row_to_connection(row)

    def list_connections(self, group_id: str = None) -> ConnectionList:
        group_id = group_id or self.DEFAULT_GROUP
        rows = db.list_connections(group_id)
        connections = []
        for row in rows:
            full_row = db.get_connection_config(row["id"])
            conn = self._row_to_connection(full_row, mask_password=True)
            if conn:
                connections.append(conn)
        return ConnectionList(connections=connections)

    def get_connection(self, connection_id: str) -> Optional[DatabaseConnection]:
        row = db.get_connection_config(connection_id)
        return self._row_to_connection(row, mask_password=False)

    def update_connection(self, connection_id: str, data: ConnectionUpdate) -> Optional[DatabaseConnection]:
        row = db.get_connection_config(connection_id)
        if not row:
            return None
        update_fields = data.model_dump(exclude_unset=True)
        field_map = {
            "name": "name", "type": "db_type", "host": "host",
            "port": "port", "database": "database", "db_schema": "db_schema",
            "username": "username", "password": "password",
        }
        db_fields = {field_map[k]: v for k, v in update_fields.items() if k in field_map}
        if "password" in db_fields:
            db_fields["password"] = _encrypt_password(db_fields["password"])
        if "description" in update_fields:
            import json
            extra = row.get("extra") or {}
            if isinstance(extra, str):
                extra = json.loads(extra)
            extra["description"] = update_fields["description"]
            db_fields["extra"] = extra
        if db_fields:
            db.update_connection(connection_id, **db_fields)
        return self._row_to_connection(db.get_connection_config(connection_id))

    def delete_connection(self, connection_id: str) -> bool:
        if not db.get_connection_config(connection_id):
            return False
        db.delete_connection(connection_id)
        return True

    def connection_exists(self, connection_id: str) -> bool:
        return db.get_connection_config(connection_id) is not None

    def test_connection(self, connection_id: str) -> ConnectionTestResult:
        connection = self.get_connection(connection_id)
        if not connection:
            return ConnectionTestResult(success=False, message=f"Connessione '{connection_id}' non trovata")
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
                return ConnectionTestResult(success=False, message=f"Tipo '{connection.type}' non supportato")
        except Exception as e:
            return ConnectionTestResult(success=False, message=f"Errore: {str(e)}")

    def _test_postgres(self, conn):
        try:
            import psycopg2
            c = psycopg2.connect(host=conn.host, port=conn.port, database=conn.database,
                                  user=conn.username, password=conn.password, connect_timeout=5)
            cur = c.cursor()
            cur.execute("SELECT version();")
            version = cur.fetchone()[0]
            cur.close(); c.close()
            return ConnectionTestResult(success=True, message="Connessione PostgreSQL riuscita", details={"version": version})
        except ImportError:
            return ConnectionTestResult(success=False, message="psycopg2 non installato: pip install psycopg2-binary")
        except Exception as e:
            return ConnectionTestResult(success=False, message=f"Errore PostgreSQL: {str(e)}")

    def _test_mssql(self, conn):
        try:
            import pyodbc
            for driver in ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"]:
                try:
                    cs = f"DRIVER={{{driver}}};SERVER={conn.host},{conn.port};DATABASE={conn.database};UID={conn.username};PWD={conn.password};TrustServerCertificate=yes"
                    c = pyodbc.connect(cs, timeout=5)
                    cur = c.cursor()
                    cur.execute("SELECT @@VERSION;")
                    version = cur.fetchone()[0]
                    cur.close(); c.close()
                    return ConnectionTestResult(success=True, message=f"MS SQL riuscita ({driver})", details={"version": version[:100]})
                except pyodbc.InterfaceError:
                    continue
            return ConnectionTestResult(success=False, message="Nessun driver ODBC trovato.")
        except ImportError:
            return ConnectionTestResult(success=False, message="pyodbc non installato: pip install pyodbc")
        except Exception as e:
            return ConnectionTestResult(success=False, message=f"Errore MS SQL: {str(e)}")

    def _test_mysql(self, conn):
        try:
            import pymysql
            c = pymysql.connect(host=conn.host, port=conn.port, database=conn.database,
                                 user=conn.username, password=conn.password, connect_timeout=5)
            cur = c.cursor()
            cur.execute("SELECT VERSION();")
            version = cur.fetchone()[0]
            cur.close(); c.close()
            return ConnectionTestResult(success=True, message="MySQL/MariaDB riuscita", details={"version": version})
        except ImportError:
            return ConnectionTestResult(success=False, message="pymysql non installato: pip install pymysql")
        except Exception as e:
            return ConnectionTestResult(success=False, message=f"Errore MySQL: {str(e)}")

    def _test_oracle(self, conn):
        try:
            import oracledb
            c = oracledb.connect(user=conn.username, password=conn.password,
                                  dsn=f"{conn.host}:{conn.port}/{conn.database}")
            cur = c.cursor()
            cur.execute("SELECT banner FROM v$version WHERE banner LIKE 'Oracle%' AND ROWNUM = 1")
            version = cur.fetchone()[0]
            cur.close(); c.close()
            return ConnectionTestResult(success=True, message="Oracle riuscita", details={"version": version})
        except ImportError:
            return ConnectionTestResult(success=False, message="oracledb non installato: pip install oracledb")
        except Exception as e:
            return ConnectionTestResult(success=False, message=f"Errore Oracle: {str(e)}")

    def _test_sqlite(self, conn):
        try:
            import sqlite3
            c = sqlite3.connect(conn.database, timeout=5)
            cur = c.cursor()
            cur.execute("SELECT sqlite_version();")
            version = cur.fetchone()[0]
            cur.close(); c.close()
            return ConnectionTestResult(success=True, message="SQLite riuscita", details={"version": version})
        except Exception as e:
            return ConnectionTestResult(success=False, message=f"Errore SQLite: {str(e)}")
