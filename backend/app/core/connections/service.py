"""Domain service for database connections."""

import sqlite3

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.core.connections import db as connection_db
from app.infrastructure.security.crypto import CredentialUnavailableError, is_encrypted
from app.infrastructure.security.crypto import decrypt as _decrypt_password
from app.infrastructure.security.crypto import encrypt as _encrypt_password
from app.core.connections.models import (
    DatabaseConnection,
    ConnectionCreate,
    ConnectionUpdate,
    ConnectionList,
    ConnectionTestResult,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _sanitize_mssql_error(e: Exception) -> str:
    """Map MSSQL driver errors to sanitized user-facing messages.

    Pattern-match su str(e) per SQLSTATE: pyodbc non espone tipi distinti
    per ogni SQLSTATE (tutte cadono in pyodbc.Error / OperationalError),
    quindi il discriminante deterministico e' il prefisso [XXXXX] o
    parole-chiave del driver.
    """
    msg = str(e)
    msg_l = msg.lower()
    # SQLSTATE prima (piu' deterministico dei text match; un messaggio
    # pyodbc puo' contenere sia SQLSTATE che testo ambiguo
    if "[08001]" in msg:
        return "Connessione MS SQL rifiutata o host irraggiungibile"
    if "[08004]" in msg:
        return "Timeout connessione MS SQL"
    if "[28000]" in msg:
        return "Autenticazione MS SQL fallita: credenziali non valide"
    if "[42s02]" in msg_l:
        return "Database MS SQL non trovato o accesso negato"
    # Text patterns (fallback se SQLSTATE non presente, es. pymssql / socket
    if "cannot open server" in msg_l or "cannot connect" in msg_l:
        return "Connessione MS SQL rifiutata o host irraggiungibile"
    if "login timeout" in msg_l or "timeout expired" in msg_l:
        return "Timeout connessione MS SQL"
    if "login failed" in msg_l:
        return "Autenticazione MS SQL fallita: credenziali non valide"
    if "invalid object name" in msg_l:
        return "Database MS SQL non trovato o accesso negato"
    if "ssl" in msg_l or "tls" in msg_l or "handshake" in msg_l or "certificate" in msg_l:
        return "Errore protocollo TLS/TDS con MS SQL"
    if isinstance(e, TimeoutError) or "timeout" in msg_l:
        return "Timeout connessione MS SQL"
    if isinstance(e, ConnectionRefusedError) or "refused" in msg_l:
        return "Connessione MS SQL rifiutata"
    return "Errore connessione MS SQL"


class ConnectionService:

    DEFAULT_GROUP = settings.DEFAULT_GROUP

    def _generate_connection_id(self, name: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_")).strip()
        safe_name = safe_name.replace(" ", "_")[:30]
        return f"{safe_name}_{timestamp}_{short_uuid}"

    def _row_to_connection(
        self,
        row: connection_db.ConnectionConfigRow | None,
        mask_password: bool = False,
    ) -> Optional[DatabaseConnection]:
        if not row:
            return None
        extra = row.extra or {}
        if isinstance(extra, str):
            extra = json.loads(extra)
        raw_password = row.password
        if mask_password:
            display_password = "***"
        else:
            if raw_password is None:
                raise CredentialUnavailableError(
                    f"connection '{row.id}': password non configurata"
                )
            display_password = _decrypt_password(raw_password)
            if display_password is None and is_encrypted(raw_password):
                raise CredentialUnavailableError(
                    f"connection '{row.id}': password non decriptabile "
                    "(chiave mancante/sbagliata o token corrotto)"
                )
        return DatabaseConnection(
            id=row.id,
            name=row.name,
            description=extra.get("description", ""),
            type=row.db_type,
            host=row.host,
            port=row.port,
            database=row.database,
            db_schema=row.db_schema,
            username=row.username,
            password=display_password,
            group_id=row.group_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            created_by=row.created_by,
        )

    def create_connection(
        self,
        data: ConnectionCreate,
        group_id: str | None = None,
        created_by: str | None = None,
    ) -> DatabaseConnection:
        from app.core.connections.errors import ConnectionDuplicateNameError

        group_id = group_id or self.DEFAULT_GROUP
        # Unicita' (name, group_id): pre-check applicativo prima del DB
        # insert. Senza, due connections con stesso name nello stesso
        # gruppo coesistono e la lookup-by-name diventa ambigua per
        # tooling/diagnosi (e per il contract test E2E CT-002).
        existing = connection_db.list_connection_configs(group_id)
        if any(r.name == data.name for r in existing):
            raise ConnectionDuplicateNameError(
                name=data.name, group_id=group_id,
            )

        connection_id = self._generate_connection_id(data.name)
        extra = {"description": getattr(data, "description", "")}
        row = connection_db.create_connection(
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
        return self._row_to_connection(row)

    def list_connections(self, group_id: str | None = None) -> ConnectionList:
        # group_id=None → no filter (usato da superadmin e dalla path allowed_groups
        # Fix N+1: fetch tutte le righe complete in una query, no loop di get_connection_config
        rows = connection_db.list_connection_configs(group_id)
        connections = []
        for full_row in rows:
            conn = self._row_to_connection(full_row, mask_password=True)
            if conn:
                connections.append(conn)
        return ConnectionList(connections=connections)

    def get_connection(self, connection_id: str) -> Optional[DatabaseConnection]:
        row = connection_db.get_connection_config(connection_id)
        return self._row_to_connection(row, mask_password=False)

    def update_connection(self, connection_id: str, data: ConnectionUpdate) -> Optional[DatabaseConnection]:
        row = connection_db.get_connection_config(connection_id)
        if not row:
            return None
        update_fields = data.model_dump(exclude_unset=True)
        field_map = {
            "name": "name", "type": "db_type", "host": "host",
            "port": "port", "database": "database", "db_schema": "db_schema",
            "username": "username", "password": "password",
        }
        db_fields = {field_map[k]: v for k, v in update_fields.items() if k in field_map}
        # The client can echo the password mask; never persist that placeholder.
        if "password" in db_fields:
            pwd = db_fields["password"]
            if pwd is None or pwd == "" or pwd == "***":
                db_fields.pop("password")
            else:
                db_fields["password"] = _encrypt_password(pwd)
        if "description" in update_fields:
            extra = row.extra or {}
            if isinstance(extra, str):
                extra = json.loads(extra)
            else:
                extra = dict(extra)
            extra["description"] = update_fields["description"]
            db_fields["extra"] = extra
        if db_fields:
            row = connection_db.update_connection(connection_id, **db_fields)
        return self._row_to_connection(row)

    def delete_connection(self, connection_id: str) -> bool:
        if not connection_db.get_connection_config(connection_id):
            return False
        connection_db.delete_connection(connection_id)
        return True

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
            # Log con solo type+repr per evitare credential leak: i driver DB
            # (psycopg2, pyodbc, pymssql) possono includere host/user/password
            # nel str(e) di alcune eccezioni (es. "FATAL: password authentication
            # failed for user 'admin' at host postgres.internal:5432"). exc_info
            # mantiene la traceback completa per il debug operativo, ma il
            # messaggio principale resta neutro.
            logger.error(
                "[GESTITO] Errore test connessione: %s",
                type(e).__name__,
                exc_info=True,
            )
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
        except Exception:
            return ConnectionTestResult(success=False, message="Errore connessione PostgreSQL")

    def _test_mssql(self, conn):
        """
        Test connessione SQL Server.

        Strategia:
        - Filtra i driver disponibili sul sistema (`pyodbc.drivers`).
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

        # SECURITY: escape dei separatori ODBC (`;`, `{`, `}`) nei valori utente
        # Senza escape, un username come "admin;Encrypt=No" inietta parametri
        # arbitrari nella connection string, bypassando Encrypt/TrustServerCertificate
        # ODBC usa `{` `}` per delimitare valori che contengono `;`
        def _odbc_escape(val: str) -> str:
            if not val:
                return val
            if ';' in val or '{' in val or '}' in val:
                return '{' + val.replace('}', '}}') + '}'
            return val

        # TrustServerCertificate=yes accetta certificati self-signed sul lato
        # SQL Server senza validare la chain — equivalente a TLS verify=False
        # per HTTP. In prod un MITM puo' presentare un cert arbitrario e
        # decryptare username/password. Gating env-aware via tls_policy:
        # in prod/staging si forza TrustServerCertificate=no, in dev/CI
        # rimane "yes" per supportare i certificati locali.
        from app.core.security.tls_policy import tls_bypass_allowed
        trust_server_cert = "yes" if tls_bypass_allowed() else "no"
        if trust_server_cert == "no":
            logger.info(
                "[SECURITY] MSSQL test: TrustServerCertificate=no "
                "(prod-like env, cert chain validata)."
            )
        base = (
            f"SERVER={_odbc_escape(conn.host)},{conn.port};"
            f"DATABASE={_odbc_escape(conn.database)};"
            f"UID={_odbc_escape(conn.username)};"
            f"PWD={_odbc_escape(conn.password)};"
            f"TrustServerCertificate={trust_server_cert}"
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
                    # Missing-driver errors are skipped because attempts are
                    # already filtered against pyodbc.drivers().
                    err_msg = str(e)
                    if "IM002" not in err_msg:
                        # Solo type+sanitized; il raw str(e) di pyodbc puo'
                        # includere credenziali su errori auth (es. "Login
                        # failed for user 'sa' [...] password=..."). exc_info
                        # mantiene traceback per debug operativo; messaggio
                        # logger e errors[] passano per _sanitize_mssql_error.
                        sanitized = _sanitize_mssql_error(e)
                        logger.warning(
                            "[GESTITO] MS SQL driver=%r fallito: %s: %s",
                            driver_name, type(e).__name__, sanitized,
                            exc_info=True,
                        )
                        errors.append(f"[{driver_name}] {sanitized}")
                    continue

            # FreeTDS is often more tolerant of older SQL Server TLS/TDS setups.
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
                    logger.warning(
                        f"[GESTITO] MS SQL pymssql fallback fallito: "
                        f"{type(e).__name__}: {e}",
                        exc_info=True,
                    )
                    errors.append(f"[pymssql] {_sanitize_mssql_error(e)}")
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
            logger.error(
                f"[GESTITO] MS SQL _test_mssql safety-net: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            return ConnectionTestResult(success=False, message=_sanitize_mssql_error(e))

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
        except Exception:
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
        except Exception:
            return ConnectionTestResult(success=False, message="Errore connessione Oracle")

    def _test_sqlite(self, conn):
        try:
            c = sqlite3.connect(conn.database, timeout=5)
            try:
                cur = c.cursor()
                cur.execute("SELECT sqlite_version();")
                version = cur.fetchone()[0]
                cur.close()
                return ConnectionTestResult(success=True, message="SQLite riuscita", details={"version": version})
            finally:
                c.close()
        except Exception:
            return ConnectionTestResult(success=False, message="Errore connessione SQLite")
