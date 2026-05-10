"""
Ispettore di schema per database relazionali.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import sqlite3

from typing import Dict, List, Optional, TypedDict, Protocol, Callable, Any, Tuple
from dataclasses import dataclass

from app.utils.logger import get_logger

logger = get_logger(__name__)


# DEFINIZIONI DI TIPO

class ColumnInfo(TypedDict):
    """
    Informazioni su una singola colonna del database.

    Campi:
    - name: nome della colonna nel database
    - type: tipo interno PLENORA (formato "tipo_base:sottotipo:srid")
            Es: "int", "str", "geometry:point:4326"
    """
    name: str
    type: str


@dataclass
class DatabaseConnection:
    """
    Configurazione di connessione al database.

    Per SQLite, il campo 'database' contiene il path al file .db
    e host/port/username/password possono essere stringhe vuote.
    """
    type: str       # postgres, mssql, mysql, oracle, sqlite
    host: str
    port: int
    database: str   # nome database o path file per SQLite
    username: str
    password: str


# PROTOCOLLI PER DEPENDENCY INJECTION
# Questi Protocol permettono di sostituire le dipendenze reali con mock nei test

class DatabaseCursor(Protocol):
    """Protocollo per cursor database — permette mock nei test."""
    def execute(self, query: str) -> None: ...
    def fetchall(self) -> List[Tuple]: ...
    def fetchone(self) -> Optional[Tuple]: ...
    def close(self) -> None: ...


class DatabaseConnectorProtocol(Protocol):
    """Protocollo per connessioni database — permette mock nei test."""
    def cursor(self) -> DatabaseCursor: ...
    def close(self) -> None: ...


class ConnectionFactoryProtocol(Protocol):
    """Factory per creare connessioni — iniettabile per test."""
    def create_connection(self, conn: DatabaseConnection) -> DatabaseConnectorProtocol: ...


# FACTORY DI CONNESSIONE (PRODUZIONE

class DefaultConnectionFactory:
    """
    Factory di default che usa i driver reali per ogni database.

    Driver utilizzati:
    - PostgreSQL: psycopg2 (timeout 10s)
    - SQL Server: pyodbc con ODBC Driver 18/17 (fallback automatico)
    - MySQL: pymysql (timeout 10s)
    - Oracle: oracledb (preferito) oppure cx_Oracle come fallback
    - SQLite: sqlite3 della libreria standard (timeout 10s)
    """

    def create_connection(self, conn: DatabaseConnection) -> Any:
        """Crea connessione reale in base al tipo di database."""
        metodi = {
            "postgres": self._connetti_postgres,
            "mssql":    self._connetti_mssql,
            "mysql":    self._connetti_mysql,
            "oracle":   self._connetti_oracle,
            "sqlite":   self._connetti_sqlite,
        }

        metodo = metodi.get(conn.type)
        if not metodo:
            raise ValueError(f"Tipo database '{conn.type}' non supportato")
        return metodo(conn)

    def _connetti_postgres(self, conn: DatabaseConnection) -> Any:
        """Connessione PostgreSQL via psycopg2."""
        import psycopg2
        return psycopg2.connect(
            host=conn.host,
            port=conn.port,
            database=conn.database,
            user=conn.username,
            password=conn.password,
            connect_timeout=10
        )

    def _connetti_mssql(self, conn: DatabaseConnection) -> Any:
        """
        Connessione SQL Server via pyodbc.

        Driver 18 ha cambiato i default: Encrypt=Mandatory.
        Per supportare server senza TLS valido (sviluppo, on-prem legacy)
        usiamo Encrypt=Optional + TrustServerCertificate=yes.
        Fallback automatico su Driver 17 per ambienti vecchi.
        """
        import pyodbc
        base = (
            f"SERVER={conn.host},{conn.port};"
            f"DATABASE={conn.database};"
            f"UID={conn.username};"
            f"PWD={conn.password};"
            f"TrustServerCertificate=yes"
        )
        attempts = [
            f"DRIVER={{ODBC Driver 18 for SQL Server}};{base};Encrypt=Optional",
            f"DRIVER={{ODBC Driver 18 for SQL Server}};{base};Encrypt=No",
            f"DRIVER={{ODBC Driver 17 for SQL Server}};{base}",
        ]
        last_error = None
        for cs in attempts:
            try:
                return pyodbc.connect(cs, timeout=10)
            except pyodbc.InterfaceError as e:
                last_error = e
                continue
            except Exception as e:
                last_error = e
                continue

        raise ConnectionError(
            f"Nessun driver ODBC per SQL Server riuscito. Ultimo errore: {last_error}"
        )

    def _connetti_mysql(self, conn: DatabaseConnection) -> Any:
        """Connessione MySQL/MariaDB via pymysql."""
        import pymysql
        return pymysql.connect(
            host=conn.host,
            port=conn.port,
            database=conn.database,
            user=conn.username,
            password=conn.password,
            connect_timeout=10
        )

    def _connetti_oracle(self, conn: DatabaseConnection) -> Any:
        """
        Connessione Oracle: prova oracledb (moderno), fallback su cx_Oracle.

        Il DSN usa il formato host:port/service_name.
        """
        try:
            import oracledb
            return oracledb.connect(
                user=conn.username,
                password=conn.password,
                dsn=f"{conn.host}:{conn.port}/{conn.database}"
            )
        except ImportError:
            import cx_Oracle
            dsn = cx_Oracle.makedsn(conn.host, conn.port, service_name=conn.database)
            return cx_Oracle.connect(
                user=conn.username,
                password=conn.password,
                dsn=dsn
            )

    def _connetti_sqlite(self, conn: DatabaseConnection) -> Any:
        """Connessione SQLite — il campo database è il path al file."""
        return sqlite3.connect(conn.database, timeout=10)


# QUERY SQL PER ISPEZIONE SCHEMA
# Ogni query restituisce colonne e tipi nel formato specifico del DBMS

class SQLQueries:
    """
    Raccolta delle query SQL per ispezionare lo schema di ogni database.

    Le query sono costanti di classe per poter essere testate e verificate
    separatamente dalla logica di esecuzione.
    """

    # Usa information_schema.columns + geometry_columns per PostGIS
    # I COALESCE con subquery estraggono tipo geometria e SRID dalla
    # tabella geometry_columns (presente solo con estensione PostGIS
    POSTGRES_COLUMNS = """
        SELECT 
            c.table_name, 
            c.column_name,
            c.data_type,
            c.udt_name,
            COALESCE(
                (SELECT gc.type 
                 FROM geometry_columns gc 
                 WHERE gc.f_table_schema = c.table_schema
                 AND gc.f_table_name = c.table_name 
                 AND gc.f_geometry_column = c.column_name),
                NULL
            ) as geometry_type,
            COALESCE(
                (SELECT gc.srid 
                 FROM geometry_columns gc 
                 WHERE gc.f_table_schema = c.table_schema
                 AND gc.f_table_name = c.table_name 
                 AND gc.f_geometry_column = c.column_name),
                NULL
            ) as geometry_srid
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
        ORDER BY c.table_name, c.ordinal_position;
    """

    # Stessa struttura della query tabelle, ma filtrata sulle viste tramite
    # information_schema.views (che contiene solo le viste, non le tabelle
    POSTGRES_VIEW_COLUMNS = """
        SELECT 
            c.table_name, 
            c.column_name,
            c.data_type,
            c.udt_name,
            COALESCE(
                (SELECT gc.type 
                 FROM geometry_columns gc 
                 WHERE gc.f_table_schema = c.table_schema
                 AND gc.f_table_name = c.table_name 
                 AND gc.f_geometry_column = c.column_name),
                NULL
            ) as geometry_type,
            COALESCE(
                (SELECT gc.srid 
                 FROM geometry_columns gc 
                 WHERE gc.f_table_schema = c.table_schema
                 AND gc.f_table_name = c.table_name 
                 AND gc.f_geometry_column = c.column_name),
                NULL
            ) as geometry_srid
        FROM information_schema.columns c
        INNER JOIN information_schema.views v
            ON c.table_schema = v.table_schema
            AND c.table_name = v.table_name
        WHERE c.table_schema = 'public'
        ORDER BY c.table_name, c.ordinal_position;
    """

    # Usa sys.tables + sys.columns + sys.types
    # Il tipo geometry/geography è direttamente nel nome del tipo
    MSSQL_COLUMNS = """
        SELECT 
            t.name AS table_name,
            c.name AS column_name,
            ty.name AS data_type
        FROM sys.tables t
        INNER JOIN sys.columns c ON t.object_id = c.object_id
        INNER JOIN sys.types ty ON c.user_type_id = ty.user_type_id
        WHERE t.is_ms_shipped = 0
        ORDER BY t.name, c.column_id;
    """

    # Usa sys.views invece di sys.tables per ottenere solo le viste
    MSSQL_VIEW_COLUMNS = """
        SELECT 
            v.name AS table_name,
            c.name AS column_name,
            ty.name AS data_type
        FROM sys.views v
        INNER JOIN sys.columns c ON v.object_id = c.object_id
        INNER JOIN sys.types ty ON c.user_type_id = ty.user_type_id
        WHERE v.is_ms_shipped = 0
        ORDER BY v.name, c.column_id;
    """

    # Versione con SRID: usa JOIN su ST_GEOMETRY_COLUMNS (disponibile in MySQL 8+
    # La versione semplice viene usata come fallback per MariaDB e MySQL < 8
    # NB: alias `c` e `g` per evitare ambiguità sulle colonne TABLE_NAME/COLUMN_NAME
    # presenti in entrambe le tabelle (l'errore ER_NON_UNIQ_ERROR 1052
    MYSQL_COLUMNS_WITH_SRID = """
        SELECT
            c.TABLE_NAME,
            c.COLUMN_NAME,
            c.DATA_TYPE,
            c.COLUMN_TYPE,
            g.SRS_ID
        FROM INFORMATION_SCHEMA.COLUMNS c
        LEFT JOIN INFORMATION_SCHEMA.ST_GEOMETRY_COLUMNS g
            ON c.TABLE_SCHEMA = g.TABLE_SCHEMA
            AND c.TABLE_NAME = g.TABLE_NAME
            AND c.COLUMN_NAME = g.COLUMN_NAME
        WHERE c.TABLE_SCHEMA = DATABASE()
        ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION;
    """

    MYSQL_COLUMNS_SIMPLE = """
        SELECT 
            TABLE_NAME, 
            COLUMN_NAME,
            DATA_TYPE,
            COLUMN_TYPE,
            NULL as SRS_ID
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
        ORDER BY TABLE_NAME, ORDINAL_POSITION;
    """

    # Filtra INFORMATION_SCHEMA.COLUMNS solo per le viste presenti in INFORMATION_SCHEMA.VIEWS
    MYSQL_VIEW_COLUMNS_WITH_SRID = """
        SELECT 
            c.TABLE_NAME, 
            c.COLUMN_NAME,
            c.DATA_TYPE,
            c.COLUMN_TYPE,
            sg.SRS_ID
        FROM INFORMATION_SCHEMA.COLUMNS c
        INNER JOIN INFORMATION_SCHEMA.VIEWS v
            ON c.TABLE_SCHEMA = v.TABLE_SCHEMA
            AND c.TABLE_NAME = v.TABLE_NAME
        LEFT JOIN INFORMATION_SCHEMA.ST_GEOMETRY_COLUMNS sg
            ON c.TABLE_SCHEMA = sg.TABLE_SCHEMA
            AND c.TABLE_NAME = sg.TABLE_NAME
            AND c.COLUMN_NAME = sg.COLUMN_NAME
        WHERE c.TABLE_SCHEMA = DATABASE()
        ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION;
    """

    MYSQL_VIEW_COLUMNS_SIMPLE = """
        SELECT 
            c.TABLE_NAME, 
            c.COLUMN_NAME,
            c.DATA_TYPE,
            c.COLUMN_TYPE,
            NULL as SRS_ID
        FROM INFORMATION_SCHEMA.COLUMNS c
        INNER JOIN INFORMATION_SCHEMA.VIEWS v
            ON c.TABLE_SCHEMA = v.TABLE_SCHEMA
            AND c.TABLE_NAME = v.TABLE_NAME
        WHERE c.TABLE_SCHEMA = DATABASE()
        ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION;
    """

    # Usa USER_TAB_COLUMNS. Il tipo NUMBER viene disambiguato tramite
    # precision e scale (es. NUMBER(1) = bool, NUMBER(*,0) = int
    ORACLE_COLUMNS = """
        SELECT 
            TABLE_NAME, 
            COLUMN_NAME,
            DATA_TYPE,
            DATA_PRECISION,
            DATA_SCALE
        FROM USER_TAB_COLUMNS
        ORDER BY TABLE_NAME, COLUMN_ID
    """

    # Filtra USER_TAB_COLUMNS solo per le viste presenti in USER_VIEWS
    ORACLE_VIEW_COLUMNS = """
        SELECT 
            utc.TABLE_NAME, 
            utc.COLUMN_NAME,
            utc.DATA_TYPE,
            utc.DATA_PRECISION,
            utc.DATA_SCALE
        FROM USER_TAB_COLUMNS utc
        INNER JOIN USER_VIEWS uv ON utc.TABLE_NAME = uv.VIEW_NAME
        ORDER BY utc.TABLE_NAME, utc.COLUMN_ID
    """

    # Non ha information_schema: si usa sqlite_master per l'elenco tabelle
    # e PRAGMA table_info(nome) per le colonne di ogni tabella
    SQLITE_TABLES = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"

    SQLITE_VIEWS = "SELECT name FROM sqlite_master WHERE type='view';"

    @staticmethod
    def sqlite_table_info(table_name: str) -> str:
        """Genera la query PRAGMA per ottenere le colonne di una tabella SQLite."""
        return f"PRAGMA table_info({table_name});"


# MAPPER DEI TIPI (FUNZIONI PURE
# Converte i tipi nativi di ogni DBMS nel formato interno PLENORA

class TypeMapper:
    """
    Mapper per convertire tipi nativi database → tipi interni PLENORA.

    Tutti i metodi sono classmethod puri (nessun side effect), quindi
    facilmente testabili in isolamento. Ogni database ha la sua mappa
    statica e un metodo di mapping con logica specifica per casi particolari.

    Tipi interni supportati: int, real, str, bool, date, geometry
    Formato geometrie: "geometry[:sottotipo[:srid]]"
    """


    POSTGRES_TYPE_MAP = {
        # Interi
        'integer': 'int', 'int4': 'int', 'int': 'int',
        'bigint': 'int', 'int8': 'int',
        'smallint': 'int', 'int2': 'int',
        'serial': 'int', 'bigserial': 'int', 'smallserial': 'int',
        # Decimali
        'numeric': 'real', 'decimal': 'real',
        'real': 'real', 'float4': 'real',
        'double precision': 'real', 'float8': 'real',
        'money': 'real',
        # Stringhe
        'character varying': 'str', 'varchar': 'str',
        'character': 'str', 'char': 'str',
        'text': 'str', 'uuid': 'str',
        'json': 'str', 'jsonb': 'str', 'xml': 'str',
        # Booleani
        'boolean': 'bool', 'bool': 'bool',
        # Date/tempo
        'date': 'date',
        'timestamp without time zone': 'date', 'timestamp': 'date',
        'timestamp with time zone': 'date', 'timestamptz': 'date',
        'time without time zone': 'date', 'time': 'date',
        'time with time zone': 'date', 'timetz': 'date',
        'interval': 'date',
        # Tipi definiti dall'utente (PostGIS geometry
        'user-defined': 'geometry',
    }

    MSSQL_TYPE_MAP = {
        'int': 'int', 'bigint': 'int', 'smallint': 'int', 'tinyint': 'int',
        'decimal': 'real', 'numeric': 'real',
        'float': 'real', 'real': 'real',
        'money': 'real', 'smallmoney': 'real',
        'varchar': 'str', 'nvarchar': 'str',
        'char': 'str', 'nchar': 'str',
        'text': 'str', 'ntext': 'str',
        'xml': 'str', 'uniqueidentifier': 'str',
        'bit': 'bool',
        'date': 'date',
        'datetime': 'date', 'datetime2': 'date', 'smalldatetime': 'date',
        'time': 'date', 'datetimeoffset': 'date',
        'geometry': 'geometry', 'geography': 'geometry',
    }

    MYSQL_TYPE_MAP = {
        'int': 'int', 'integer': 'int',
        'bigint': 'int', 'smallint': 'int', 'tinyint': 'int', 'mediumint': 'int',
        'decimal': 'real', 'numeric': 'real',
        'float': 'real', 'double': 'real',
        'varchar': 'str', 'char': 'str',
        'text': 'str', 'tinytext': 'str', 'mediumtext': 'str', 'longtext': 'str',
        'json': 'str', 'enum': 'str', 'set': 'str',
        'bit': 'bool',
        'date': 'date',
        'datetime': 'date', 'timestamp': 'date',
        'time': 'date', 'year': 'date',
        # Tipi spaziali MySQL con sottotipo
        'geometry': 'geometry', 'point': 'geometry:point',
        'linestring': 'geometry:line', 'polygon': 'geometry:polygon',
        'multipoint': 'geometry:multipoint', 'multilinestring': 'geometry:multiline',
        'multipolygon': 'geometry:multipolygon', 'geometrycollection': 'geometry',
    }

    ORACLE_TYPE_MAP = {
        'varchar2': 'str', 'nvarchar2': 'str',
        'char': 'str', 'nchar': 'str',
        'clob': 'str', 'nclob': 'str', 'long': 'str',
        'date': 'date', 'timestamp': 'date',
        'timestamp with time zone': 'date',
        'timestamp with local time zone': 'date',
        'interval year to month': 'date',
        'interval day to second': 'date',
        'sdo_geometry': 'geometry', 'mdsys.sdo_geometry': 'geometry',
    }

    SQLITE_TYPE_MAP = {
        'integer': 'int', 'int': 'int', 'tinyint': 'int', 'smallint': 'int',
        'mediumint': 'int', 'bigint': 'int',
        'real': 'real', 'double': 'real', 'float': 'real',
        'numeric': 'real', 'decimal': 'real',
        'text': 'str', 'varchar': 'str', 'char': 'str',
        'clob': 'str', 'blob': 'str',
        'date': 'date', 'datetime': 'date', 'timestamp': 'date',
        'boolean': 'bool', 'bool': 'bool',
    }


    @classmethod
    def map_postgres(cls, data_type: str, udt_name: str,
                     geom_type: Optional[str], geom_srid: Optional[int]) -> str:
        """
        Mappa tipo PostgreSQL → tipo interno.

        Logica speciale per PostGIS: se data_type è 'USER-DEFINED' e udt_name è
        'geometry', consulta geometry_columns per tipo e SRID. Costruisce il tipo
        nel formato "geometry:point:4326".
        """
        if data_type == 'USER-DEFINED' and udt_name == 'geometry':
            if geom_type and geom_srid:
                geom_type_lower = geom_type.lower()
                sottotipi = {
                    'point': 'geometry:point',
                    'linestring': 'geometry:line',
                    'polygon': 'geometry:polygon',
                    'multipoint': 'geometry:multipoint',
                    'multilinestring': 'geometry:multiline',
                    'multipolygon': 'geometry:multipolygon',
                }
                base_type = sottotipi.get(geom_type_lower, 'geometry')
                return f"{base_type}:{geom_srid}"
            return 'geometry'

        return cls.POSTGRES_TYPE_MAP.get(data_type.lower(), 'str')

    @classmethod
    def map_mssql(cls, data_type: str) -> str:
        """Mappa tipo SQL Server → tipo interno. Semplice lookup nella mappa."""
        return cls.MSSQL_TYPE_MAP.get(data_type.lower(), 'str')

    @classmethod
    def map_mysql(cls, data_type: str, column_type: str, srs_id: Optional[int] = None) -> str:
        """
        Mappa tipo MySQL → tipo interno.

        Casi speciali:
        - tinyint(1) → bool (convenzione MySQL per booleani)
        - Tipi geometrici + SRS_ID → "geometry:sottotipo:srid"
        """
        data_type_lower = data_type.lower()
        column_type_lower = column_type.lower() if column_type else ''

        # Convenzione MySQL: tinyint(1) rappresenta un booleano
        if data_type_lower == 'tinyint' and '(1)' in column_type_lower:
            return 'bool'

        # Tipi geometrici con eventuale SRID
        geometry_types = [
            'geometry', 'point', 'linestring', 'polygon',
            'multipoint', 'multilinestring', 'multipolygon', 'geometrycollection'
        ]
        if data_type_lower in geometry_types:
            srid_suffix = f":{srs_id}" if srs_id and srs_id > 0 else ""
            base_type = cls.MYSQL_TYPE_MAP.get(data_type_lower, 'geometry')
            return f"{base_type}{srid_suffix}"

        return cls.MYSQL_TYPE_MAP.get(data_type_lower, 'str')

    @classmethod
    def map_oracle(cls, data_type: str, precision: Optional[int], scale: Optional[int]) -> str:
        """
        Mappa tipo Oracle → tipo interno.

        Logica speciale per NUMBER:
        - NUMBER(1) → bool (flag booleano)
        - NUMBER(*,0) → int (intero, scale=0 = nessun decimale)
        - NUMBER(p,s) → real (decimale)
        """
        data_type_upper = data_type.upper()

        if data_type_upper == 'NUMBER':
            if precision == 1:
                return 'bool'
            if scale is not None and scale == 0:
                return 'int'
            return 'real'

        return cls.ORACLE_TYPE_MAP.get(data_type.lower(), 'str')

    @classmethod
    def map_sqlite(cls, type_str: str) -> str:
        """
        Mappa tipo SQLite → tipo interno.

        SQLite ha tipizzazione debole, quindi i tipi possono essere qualsiasi
        stringa. Prima consulta la mappa esatta, poi usa euristiche basate
        su substring (es. "UNSIGNED BIG INT" contiene "int" → int).
        """
        type_lower = type_str.lower().strip()

        # Rimuovi parametri di tipo (es. "VARCHAR(255)" → "varchar"
        if '(' in type_lower:
            type_lower = type_lower.split('(')[0].strip()

        if type_lower in cls.SQLITE_TYPE_MAP:
            return cls.SQLITE_TYPE_MAP[type_lower]

        # Euristiche per tipi non standard di SQLite
        if 'int' in type_lower:
            return 'int'
        elif 'real' in type_lower or 'float' in type_lower or 'double' in type_lower:
            return 'real'
        elif 'bool' in type_lower:
            return 'bool'
        elif 'date' in type_lower or 'time' in type_lower:
            return 'date'

        # Default: tutto ciò che non riconosciamo è stringa
        return 'str'


# PROCESSORI DI RISULTATI (FUNZIONI PURE
# Trasformano righe SQL grezze → Dict[tabella → lista colonne

class ResultProcessor:
    """
    Processa i risultati delle query di ispezione in strutture dati uniformi.

    Tutti i metodi sono staticmethod puri: ricevono righe SQL e restituiscono
    Dict[str, List[ColumnInfo]] senza effetti collaterali, facilmente testabili.
    """

    @staticmethod
    def process_postgres_rows(rows: List[Tuple]) -> Dict[str, List[ColumnInfo]]:
        """
        Processa righe PostgreSQL.
        Formato riga: (table_name, column_name, data_type, udt_name, geom_type, geom_srid)
        """
        tables: Dict[str, List[ColumnInfo]] = {}
        for table_name, column_name, data_type, udt_name, geom_type, geom_srid in rows:
            if table_name not in tables:
                tables[table_name] = []
            col_type = TypeMapper.map_postgres(data_type, udt_name, geom_type, geom_srid)
            tables[table_name].append({"name": column_name, "type": col_type})
        return tables

    @staticmethod
    def process_mssql_rows(rows: List[Tuple]) -> Dict[str, List[ColumnInfo]]:
        """
        Processa righe SQL Server.
        Formato riga: (table_name, column_name, data_type)
        """
        tables: Dict[str, List[ColumnInfo]] = {}
        for table_name, column_name, data_type in rows:
            if table_name not in tables:
                tables[table_name] = []
            col_type = TypeMapper.map_mssql(data_type)
            tables[table_name].append({"name": column_name, "type": col_type})
        return tables

    @staticmethod
    def process_mysql_rows(rows: List[Tuple], has_srid: bool = False) -> Dict[str, List[ColumnInfo]]:
        """
        Processa righe MySQL/MariaDB.
        Formato riga: (TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, SRS_ID)
        Il flag has_srid indica se SRS_ID contiene dati reali o è sempre NULL.
        """
        tables: Dict[str, List[ColumnInfo]] = {}
        for row in rows:
            table_name = row[0]
            column_name = row[1]
            data_type = row[2]
            column_type = row[3]
            srs_id = row[4] if has_srid else None

            if table_name not in tables:
                tables[table_name] = []
            col_type = TypeMapper.map_mysql(data_type, column_type, srs_id)
            tables[table_name].append({"name": column_name, "type": col_type})
        return tables

    @staticmethod
    def process_oracle_rows(rows: List[Tuple]) -> Dict[str, List[ColumnInfo]]:
        """
        Processa righe Oracle.
        Formato riga: (TABLE_NAME, COLUMN_NAME, DATA_TYPE, DATA_PRECISION, DATA_SCALE)
        """
        tables: Dict[str, List[ColumnInfo]] = {}
        for table_name, column_name, data_type, precision, scale in rows:
            if table_name not in tables:
                tables[table_name] = []
            col_type = TypeMapper.map_oracle(data_type, precision, scale)
            tables[table_name].append({"name": column_name, "type": col_type})
        return tables

    @staticmethod
    def process_sqlite_table(table_info_rows: List[Tuple]) -> List[ColumnInfo]:
        """
        Processa le colonne di una singola tabella SQLite.
        Formato riga da PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        """
        columns = []
        for row in table_info_rows:
            col_name = row[1]
            col_type_raw = row[2].lower() if row[2] else 'text'
            col_type = TypeMapper.map_sqlite(col_type_raw)
            columns.append({"name": col_name, "type": col_type})
        return columns


# CLASSE PRINCIPALE — ISPETTORE DATABASE

class DatabaseInspector:
    """
    Ispeziona lo schema di un database e restituisce tabelle/colonne con tipi.

    Usa Dependency Injection per tutte le dipendenze esterne:
    - connection_factory: sostituibile con mock per test (evita connessioni reali)
    - type_mapper: sostituibile per mapping personalizzati
    - result_processor: sostituibile per elaborazioni custom

    Uso tipico in produzione:
        inspector = DatabaseInspector
        schema = inspector.get_tables_and_columns(connection)

    Uso nei test:
        mock_factory = MockConnectionFactory
        inspector = DatabaseInspector(connection_factory=mock_factory)
    """

    def __init__(
        self,
        connection_factory: ConnectionFactoryProtocol = None,
        type_mapper: type = None,
        result_processor: type = None
    ):
        self._factory = connection_factory or DefaultConnectionFactory()
        self._mapper = type_mapper or TypeMapper
        self._processor = result_processor or ResultProcessor

    def get_tables_and_columns(self, connection: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """
        Metodo principale: ottiene lo schema completo del database.

        Args:
            connection: configurazione di connessione con credenziali

        Returns:
            Dizionario {nome_tabella: [ColumnInfo, ...]} per tutte le tabelle
        """
        metodi = {
            "postgres": self._inspect_postgres,
            "mssql":    self._inspect_mssql,
            "mysql":    self._inspect_mysql,
            "oracle":   self._inspect_oracle,
            "sqlite":   self._inspect_sqlite,
        }

        metodo = metodi.get(connection.type)
        if not metodo:
            raise ValueError(f"Tipo database '{connection.type}' non supportato")
        return metodo(connection)

    def get_views_and_columns(self, connection: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """
        Ottiene lo schema di tutte le viste del database.

        Args:
            connection: configurazione di connessione con credenziali

        Returns:
            Dizionario {nome_vista: [ColumnInfo, ...]} per tutte le viste
        """
        metodi = {
            "postgres": self._inspect_postgres_views,
            "mssql":    self._inspect_mssql_views,
            "mysql":    self._inspect_mysql_views,
            "oracle":   self._inspect_oracle_views,
            "sqlite":   self._inspect_sqlite_views,
        }

        metodo = metodi.get(connection.type)
        if not metodo:
            raise ValueError(f"Tipo database '{connection.type}' non supportato")
        return metodo(connection)


    def _esegui_ispezione(
        self,
        conn: DatabaseConnection,
        query: str,
        processor: Callable,
        driver_name: str,
        **processor_kwargs
    ) -> Dict[str, List[ColumnInfo]]:
        """
        Helper comune per ispezione con singola query.

        Pattern condiviso da PostgreSQL, SQL Server e Oracle:
        1. Crea connessione
        2. Esegui query
        3. Processa risultati
        4. Chiudi connessione (anche in caso di errore, via finally)

        Args:
            conn: configurazione connessione
            query: query SQL da eseguire
            processor: funzione per processare le righe
            driver_name: nome del driver per messaggi di errore
            **processor_kwargs: argomenti extra per il processor
        """
        try:
            connection = self._factory.create_connection(conn)
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute(query)
                    rows = cursor.fetchall()
                    return processor(rows, **processor_kwargs)
                finally:
                    cursor.close()
            finally:
                connection.close()
        except ImportError:
            raise ImportError(f"Driver {driver_name} non installato")


    def _inspect_postgres(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """Ispeziona PostgreSQL — include tipo geometria e SRID da PostGIS."""
        return self._esegui_ispezione(
            conn, SQLQueries.POSTGRES_COLUMNS,
            self._processor.process_postgres_rows, "PostgreSQL (psycopg2)"
        )

    def _inspect_mssql(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """Ispeziona SQL Server — usa sys.tables/columns/types."""
        return self._esegui_ispezione(
            conn, SQLQueries.MSSQL_COLUMNS,
            self._processor.process_mssql_rows, "MS SQL (pyodbc)"
        )

    def _inspect_mysql(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """
        Ispeziona MySQL/MariaDB con rilevamento automatico variante.

        MariaDB non supporta ST_GEOMETRY_COLUMNS, quindi usa la query semplice.
        MySQL 8+ prova prima la query con SRID, con fallback su quella semplice.
        """
        try:
            connection = self._factory.create_connection(conn)
            try:
                cursor = connection.cursor()
                try:
                    # Rileva se è MariaDB dalla stringa VERSION
                    cursor.execute("SELECT VERSION()")
                    version = cursor.fetchone()[0].lower()
                    is_mariadb = 'mariadb' in version

                    has_srid = False
                    if is_mariadb:
                        cursor.execute(SQLQueries.MYSQL_COLUMNS_SIMPLE)
                        rows = cursor.fetchall()
                    else:
                        try:
                            cursor.execute(SQLQueries.MYSQL_COLUMNS_WITH_SRID)
                            rows = cursor.fetchall()
                            has_srid = True
                        except Exception as e:
                            logger.info("MySQL SRID query non supportata, fallback a query semplice: %s", e)
                            cursor.execute(SQLQueries.MYSQL_COLUMNS_SIMPLE)
                            rows = cursor.fetchall()

                    return self._processor.process_mysql_rows(rows, has_srid)
                finally:
                    cursor.close()
            finally:
                connection.close()
        except ImportError:
            raise ImportError("Driver MySQL (pymysql) non installato")

    def _inspect_oracle(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """Ispeziona Oracle — usa USER_TAB_COLUMNS con precision/scale per NUMBER."""
        return self._esegui_ispezione(
            conn, SQLQueries.ORACLE_COLUMNS,
            self._processor.process_oracle_rows, "Oracle (oracledb)"
        )

    def _inspect_sqlite(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """
        Ispeziona SQLite — richiede due passaggi:
        1. sqlite_master per l'elenco tabelle
        2. PRAGMA table_info(tabella) per le colonne di ciascuna
        """
        try:
            connection = self._factory.create_connection(conn)
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute(SQLQueries.SQLITE_TABLES)
                    table_names = [row[0] for row in cursor.fetchall()]

                    tables: Dict[str, List[ColumnInfo]] = {}
                    for table_name in table_names:
                        cursor.execute(SQLQueries.sqlite_table_info(table_name))
                        tables[table_name] = self._processor.process_sqlite_table(cursor.fetchall())

                    return tables
                finally:
                    cursor.close()
            finally:
                connection.close()
        except Exception as e:
            raise RuntimeError(f"Errore ispezionando SQLite: {e}")


    def _inspect_postgres_views(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """Ispeziona viste PostgreSQL — include tipo geometria e SRID da PostGIS."""
        return self._esegui_ispezione(
            conn, SQLQueries.POSTGRES_VIEW_COLUMNS,
            self._processor.process_postgres_rows, "PostgreSQL (psycopg2)"
        )

    def _inspect_mssql_views(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """Ispeziona viste SQL Server — usa sys.views/columns/types."""
        return self._esegui_ispezione(
            conn, SQLQueries.MSSQL_VIEW_COLUMNS,
            self._processor.process_mssql_rows, "MS SQL (pyodbc)"
        )

    def _inspect_mysql_views(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """
        Ispeziona viste MySQL/MariaDB con rilevamento automatico variante.
        Stessa logica di _inspect_mysql ma con query filtrate per viste.
        """
        try:
            connection = self._factory.create_connection(conn)
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute("SELECT VERSION()")
                    version = cursor.fetchone()[0].lower()
                    is_mariadb = 'mariadb' in version

                    has_srid = False
                    if is_mariadb:
                        cursor.execute(SQLQueries.MYSQL_VIEW_COLUMNS_SIMPLE)
                        rows = cursor.fetchall()
                    else:
                        try:
                            cursor.execute(SQLQueries.MYSQL_VIEW_COLUMNS_WITH_SRID)
                            rows = cursor.fetchall()
                            has_srid = True
                        except Exception as e:
                            logger.info("MySQL view SRID query non supportata, fallback a query semplice: %s", e)
                            cursor.execute(SQLQueries.MYSQL_VIEW_COLUMNS_SIMPLE)
                            rows = cursor.fetchall()

                    return self._processor.process_mysql_rows(rows, has_srid)
                finally:
                    cursor.close()
            finally:
                connection.close()
        except ImportError:
            raise ImportError("Driver MySQL (pymysql) non installato")

    def _inspect_oracle_views(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """Ispeziona viste Oracle — usa USER_VIEWS + USER_TAB_COLUMNS."""
        return self._esegui_ispezione(
            conn, SQLQueries.ORACLE_VIEW_COLUMNS,
            self._processor.process_oracle_rows, "Oracle (oracledb)"
        )

    def _inspect_sqlite_views(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """
        Ispeziona viste SQLite — usa sqlite_master WHERE type='view'.
        Per le colonne usa PRAGMA table_info che funziona anche per le viste.
        """
        try:
            connection = self._factory.create_connection(conn)
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute(SQLQueries.SQLITE_VIEWS)
                    view_names = [row[0] for row in cursor.fetchall()]

                    views: Dict[str, List[ColumnInfo]] = {}
                    for view_name in view_names:
                        cursor.execute(SQLQueries.sqlite_table_info(view_name))
                        views[view_name] = self._processor.process_sqlite_table(cursor.fetchall())

                    return views
                finally:
                    cursor.close()
            finally:
                connection.close()
        except Exception as e:
            raise RuntimeError(f"Errore ispezionando viste SQLite: {e}")
