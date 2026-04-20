"""
Servizio CRUD per connessioni database.
Storage: PostgreSQL tramite app.db.db_service (facade) e
app.db.config_db_service (query dirette catalogo PLENORA).

Le password vengono cifrate con Fernet prima di essere salvate nel DB.
La logica di cifratura è centralizzata in app.shared.crypto — qui si fa
solo l'aliasing per chiarezza nel codice del service.

Retrocompatibilità: i valori senza prefisso "enc:" sono trattati come
plaintext legacy e vengono cifrati automaticamente al primo update.
"""

import uuid
from datetime import datetime, timezone
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
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
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
            created_by=row.get("created_by"),
        )

    def create_connection(self, data: ConnectionCreate, group_id: str = None, created_by: str = None) -> DatabaseConnection:
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
            created_by=created_by,
        )
        logger.info(f"Connessione creata: {connection_id}", op="create_connection")
        row = db.get_connection_config(connection_id)
        return self._row_to_connection(row)

    def list_connections(self, group_id: str = None) -> ConnectionList:
        # group_id=None → no filter (usato da superadmin e dalla path allowed_groups)
        # Fix N+1: fetch tutte le righe complete in una query, no loop di get_connection_config
        import app.db.config_db_service as _cfg_db
        if group_id is None:
            rows = _cfg_db.fetchall(
                "SELECT * FROM connections ORDER BY name"
            )
        else:
            rows = _cfg_db.fetchall(
                "SELECT * FROM connections WHERE group_id = %s ORDER BY name",
                (group_id,)
            )
        connections = []
        for full_row in rows:
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
        # Bugfix: il frontend riceve la password mascherata "***" nelle list/get
        # e la rispedisce inalterata se l'utente non la modifica. Skipparla per
        # non sovrascrivere la vera password salvata.
        if "password" in db_fields:
            pwd = db_fields["password"]
            if pwd is None or pwd == "" or pwd == "***":
                db_fields.pop("password")
            else:
                db_fields["password"] = _encrypt_password(pwd)
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
            logger.error(f"[GESTITO] Errore test connessione: {e}", exc_info=True)
            return ConnectionTestResult(success=False, message="Errore durante il test della connessione")

    def _test_postgres(self, conn):
        try:
            import psycopg2
            c = psycopg2.connect(host=conn.host, port=conn.port, database=conn.database,
                                  user=conn.username, password=conn.password, connect_timeout=5)
            try:
                with c.cursor() as cur:
                    cur.execute("SELECT version();")
                    version = cur.fetchone()[0]
                return ConnectionTestResult(success=True, message="Connessione PostgreSQL riuscita", details={"version": version})
            finally:
                c.close()
        except ImportError:
            return ConnectionTestResult(success=False, message="psycopg2 non installato: pip install psycopg2-binary")
        except Exception as e:
            return ConnectionTestResult(success=False, message="Errore connessione PostgreSQL")

    def _test_mssql(self, conn):
        """
        Test connessione SQL Server.

        Strategia:
        - Filtra i driver disponibili sul sistema (`pyodbc.drivers()`).
        - Per ogni driver disponibile prova varianti di Encrypt:
          * Driver 18: Encrypt=Optional (Default ora è Mandatory che vuole TLS)
          * Driver 18: Encrypt=No (server senza TLS)
          * Driver 17: senza Encrypt (driver legacy, default no encryption)
          * "SQL Server" (driver vintage MS, ultimo fallback)
        - Riporta il PRIMO errore reale (non IM002 di driver mancanti).
        """
        try:
            import pyodbc
        except ImportError:
            return ConnectionTestResult(success=False, message="pyodbc non installato: pip install pyodbc")

        installed = set(pyodbc.drivers())
        if not any("SQL Server" in d for d in installed):
            return ConnectionTestResult(
                success=False,
                message=(
                    "Nessun driver ODBC per SQL Server installato sul sistema. "
                    "Installa 'ODBC Driver 18 for SQL Server' da Microsoft."
                ),
            )

        # SECURITY: escape dei separatori ODBC (`;`, `{`, `}`) nei valori utente.
        # Senza escape, un username come "admin;Encrypt=No" inietta parametri
        # arbitrari nella connection string, bypassando Encrypt/TrustServerCertificate.
        # ODBC usa `{` `}` per delimitare valori che contengono `;`.
        def _odbc_escape(val: str) -> str:
            if not val:
                return val
            if ';' in val or '{' in val or '}' in val:
                return '{' + val.replace('}', '}}') + '}'
            return val

        base = (
            f"SERVER={_odbc_escape(conn.host)},{conn.port};"
            f"DATABASE={_odbc_escape(conn.database)};"
            f"UID={_odbc_escape(conn.username)};"
            f"PWD={_odbc_escape(conn.password)};"
            f"TrustServerCertificate=yes"
        )

        all_attempts = [
            ("ODBC Driver 18 for SQL Server", f"DRIVER={{ODBC Driver 18 for SQL Server}};{base};Encrypt=Optional"),
            ("ODBC Driver 18 for SQL Server", f"DRIVER={{ODBC Driver 18 for SQL Server}};{base};Encrypt=No"),
            ("ODBC Driver 17 for SQL Server", f"DRIVER={{ODBC Driver 17 for SQL Server}};{base}"),
            ("SQL Server",                    f"DRIVER={{SQL Server}};{base}"),
        ]

        # Filtra solo i driver effettivamente installati per evitare IM002 fittizi
        attempts = [(d, cs) for d, cs in all_attempts if d in installed]
        if not attempts:
            return ConnectionTestResult(
                success=False,
                message=f"Nessuna combinazione driver/encrypt compatibile con quelli installati: {sorted(installed)}",
            )

        errors = []
        try:
            for driver_name, cs in attempts:
                try:
                    c = pyodbc.connect(cs, timeout=5)
                    try:
                        cur = c.cursor()
                        cur.execute("SELECT @@VERSION;")
                        version = cur.fetchone()[0]
                        cur.close()
                        return ConnectionTestResult(
                            success=True,
                            message=f"MS SQL riuscita ({driver_name})",
                            details={"version": version[:100]},
                        )
                    finally:
                        c.close()
                except Exception as e:
                    # Filtra IM002 (driver non installato): non aiuta capire il problema
                    err_msg = str(e)
                    if "IM002" not in err_msg:
                        errors.append(f"[{driver_name}] {err_msg}")
                    continue

            # Fallback finale: pymssql (usa FreeTDS, più tollerante con server vecchi
            # che hanno problemi di handshake TLS/TDS con i driver Microsoft moderni).
            try:
                import pymssql
                try:
                    c = pymssql.connect(
                        server=conn.host,
                        port=conn.port,
                        user=conn.username,
                        password=conn.password,
                        database=conn.database,
                        timeout=5,
                        login_timeout=5,
                    )
                    try:
                        cur = c.cursor()
                        cur.execute("SELECT @@VERSION")
                        version = cur.fetchone()[0]
                        cur.close()
                        return ConnectionTestResult(
                            success=True,
                            message="MS SQL riuscita (pymssql / FreeTDS)",
                            details={"version": version[:100]},
                        )
                    finally:
                        c.close()
                except Exception as e:
                    errors.append(f"[pymssql] {e}")
            except ImportError:
                errors.append("[pymssql non installato] Per server vecchi installa: pip install pymssql")

            return ConnectionTestResult(
                success=False,
                message=(
                    f"Connessione SQL Server fallita su tutti i driver disponibili. "
                    f"Primo errore: {errors[0] if errors else 'sconosciuto'}. "
                    f"Tentativi totali: {len(errors)}."
                ),
                details={"errors": errors},
            )
        except Exception as e:
            return ConnectionTestResult(success=False, message=f"Errore MS SQL: {str(e)}")

    def _test_mysql(self, conn):
        try:
            import pymysql
            c = pymysql.connect(host=conn.host, port=conn.port, database=conn.database,
                                 user=conn.username, password=conn.password, connect_timeout=5)
            try:
                cur = c.cursor()
                cur.execute("SELECT VERSION();")
                version = cur.fetchone()[0]
                cur.close()
                return ConnectionTestResult(success=True, message="MySQL/MariaDB riuscita", details={"version": version})
            finally:
                c.close()
        except ImportError:
            return ConnectionTestResult(success=False, message="pymysql non installato: pip install pymysql")
        except Exception as e:
            return ConnectionTestResult(success=False, message="Errore connessione MySQL")

    def _test_oracle(self, conn):
        try:
            import oracledb
            c = oracledb.connect(user=conn.username, password=conn.password,
                                  dsn=f"{conn.host}:{conn.port}/{conn.database}")
            try:
                cur = c.cursor()
                cur.execute("SELECT banner FROM v$version WHERE banner LIKE 'Oracle%' AND ROWNUM = 1")
                version = cur.fetchone()[0]
                cur.close()
                return ConnectionTestResult(success=True, message="Oracle riuscita", details={"version": version})
            finally:
                c.close()
        except ImportError:
            return ConnectionTestResult(success=False, message="oracledb non installato: pip install oracledb")
        except Exception as e:
            return ConnectionTestResult(success=False, message="Errore connessione Oracle")

    def _test_sqlite(self, conn):
        try:
            import sqlite3
            c = sqlite3.connect(conn.database, timeout=5)
            try:
                cur = c.cursor()
                cur.execute("SELECT sqlite_version();")
                version = cur.fetchone()[0]
                cur.close()
                return ConnectionTestResult(success=True, message="SQLite riuscita", details={"version": version})
            finally:
                c.close()
        except Exception as e:
            return ConnectionTestResult(success=False, message="Errore connessione SQLite")
