"""
Service per ispezionare lo schema dei database
Gestisce le query specifiche per ogni tipo di database
Supporta estrazione dei tipi colonna inclusi geometry/spatial
"""
from typing import Dict, List, Optional, TypedDict
from models import DatabaseConnection


class ColumnInfo(TypedDict):
    """Informazioni su una colonna"""
    name: str
    type: str  # int, real, str, bool, date, geometry:point, geometry:line, geometry:polygon, etc.


class DatabaseInspector:
    """Ispeziona schema database e ritorna tabelle/colonne con tipi"""
    
    # ==================== MAPPING TIPI ====================
    
    # PostgreSQL type mapping
    POSTGRES_TYPE_MAP = {
        # Integer types
        'integer': 'int', 'int4': 'int', 'int': 'int',
        'bigint': 'int', 'int8': 'int',
        'smallint': 'int', 'int2': 'int',
        'serial': 'int', 'bigserial': 'int', 'smallserial': 'int',
        # Real/Decimal types
        'numeric': 'real', 'decimal': 'real',
        'real': 'real', 'float4': 'real',
        'double precision': 'real', 'float8': 'real',
        'money': 'real',
        # String types
        'character varying': 'str', 'varchar': 'str',
        'character': 'str', 'char': 'str',
        'text': 'str',
        'uuid': 'str',
        'json': 'str', 'jsonb': 'str',
        'xml': 'str',
        # Boolean
        'boolean': 'bool', 'bool': 'bool',
        # Date/Time types
        'date': 'date',
        'timestamp without time zone': 'date', 'timestamp': 'date',
        'timestamp with time zone': 'date', 'timestamptz': 'date',
        'time without time zone': 'date', 'time': 'date',
        'time with time zone': 'date', 'timetz': 'date',
        'interval': 'date',
        # Geometry (PostGIS) - handled separately
        'user-defined': 'geometry',  # PostGIS geometry appears as USER-DEFINED
    }
    
    # MS SQL Server type mapping
    MSSQL_TYPE_MAP = {
        # Integer types
        'int': 'int', 'bigint': 'int', 'smallint': 'int', 'tinyint': 'int',
        # Real/Decimal types
        'decimal': 'real', 'numeric': 'real',
        'float': 'real', 'real': 'real',
        'money': 'real', 'smallmoney': 'real',
        # String types
        'varchar': 'str', 'nvarchar': 'str',
        'char': 'str', 'nchar': 'str',
        'text': 'str', 'ntext': 'str',
        'xml': 'str',
        'uniqueidentifier': 'str',
        # Boolean
        'bit': 'bool',
        # Date/Time types
        'date': 'date',
        'datetime': 'date', 'datetime2': 'date', 'smalldatetime': 'date',
        'time': 'date',
        'datetimeoffset': 'date',
        # Geometry (SQL Server Spatial)
        'geometry': 'geometry', 'geography': 'geometry',
    }
    
    # MySQL type mapping
    MYSQL_TYPE_MAP = {
        # Integer types
        'int': 'int', 'integer': 'int',
        'bigint': 'int', 'smallint': 'int', 'tinyint': 'int', 'mediumint': 'int',
        # Real/Decimal types
        'decimal': 'real', 'numeric': 'real',
        'float': 'real', 'double': 'real',
        # String types
        'varchar': 'str', 'char': 'str',
        'text': 'str', 'tinytext': 'str', 'mediumtext': 'str', 'longtext': 'str',
        'json': 'str',
        'enum': 'str', 'set': 'str',
        # Boolean (MySQL uses tinyint(1))
        'bit': 'bool',
        # Date/Time types
        'date': 'date',
        'datetime': 'date', 'timestamp': 'date',
        'time': 'date', 'year': 'date',
        # Geometry (MySQL Spatial)
        'geometry': 'geometry', 'point': 'geometry:point',
        'linestring': 'geometry:line', 'polygon': 'geometry:polygon',
        'multipoint': 'geometry:multipoint', 'multilinestring': 'geometry:multiline',
        'multipolygon': 'geometry:multipolygon', 'geometrycollection': 'geometry',
    }
    
    # Oracle type mapping
    ORACLE_TYPE_MAP = {
        # Number types - Oracle uses NUMBER for everything
        'number': 'real',  # Will be refined based on scale
        # String types
        'varchar2': 'str', 'nvarchar2': 'str',
        'char': 'str', 'nchar': 'str',
        'clob': 'str', 'nclob': 'str',
        'long': 'str',
        # Date/Time types
        'date': 'date',
        'timestamp': 'date',
        'timestamp with time zone': 'date',
        'timestamp with local time zone': 'date',
        'interval year to month': 'date',
        'interval day to second': 'date',
        # Geometry (Oracle Spatial)
        'sdo_geometry': 'geometry', 'mdsys.sdo_geometry': 'geometry',
    }
    
    # SQLite type mapping (SQLite has dynamic typing)
    SQLITE_TYPE_MAP = {
        # Integer types
        'integer': 'int', 'int': 'int', 'tinyint': 'int', 'smallint': 'int',
        'mediumint': 'int', 'bigint': 'int',
        # Real types
        'real': 'real', 'double': 'real', 'float': 'real',
        'numeric': 'real', 'decimal': 'real',
        # String types
        'text': 'str', 'varchar': 'str', 'char': 'str',
        'clob': 'str', 'blob': 'str',
        # Date (SQLite stores as TEXT/REAL/INTEGER)
        'date': 'date', 'datetime': 'date', 'timestamp': 'date',
        # Boolean (SQLite uses INTEGER 0/1)
        'boolean': 'bool', 'bool': 'bool',
    }
    
    # ==================== MAIN METHOD ====================
    
    def get_tables_and_columns(self, connection: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """
        Ottiene tabelle e colonne con tipi dal database
        
        Args:
            connection: DatabaseConnection con credenziali
            
        Returns:
            Dict con {table_name: [{"name": "col1", "type": "int"}, ...]}
        """
        if connection.type == "postgres":
            return self._inspect_postgres(connection)
        elif connection.type == "mssql":
            return self._inspect_mssql(connection)
        elif connection.type == "mysql":
            return self._inspect_mysql(connection)
        elif connection.type == "oracle":
            return self._inspect_oracle(connection)
        elif connection.type == "sqlite":
            return self._inspect_sqlite(connection)
        else:
            raise ValueError(f"Tipo database '{connection.type}' non supportato")
    
    # ==================== POSTGRES ====================
    
    def _inspect_postgres(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """Ispeziona PostgreSQL con supporto PostGIS"""
        try:
            import psycopg2
            
            connection = psycopg2.connect(
                host=conn.host,
                port=conn.port,
                database=conn.database,
                user=conn.username,
                password=conn.password,
                connect_timeout=10
            )
            
            cursor = connection.cursor()
            
            # Query con tipi, supporto PostGIS geometry e SRID
            query = """
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
            
            cursor.execute(query)
            rows = cursor.fetchall()
            
            cursor.close()
            connection.close()
            
            # Raggruppa per tabella con tipi
            tables: Dict[str, List[ColumnInfo]] = {}
            for table_name, column_name, data_type, udt_name, geom_type, geom_srid in rows:
                if table_name not in tables:
                    tables[table_name] = []
                
                col_type = self._map_postgres_type(data_type, udt_name, geom_type, geom_srid)
                
                tables[table_name].append({
                    "name": column_name,
                    "type": col_type
                })
            
            return tables
        
        except ImportError:
            raise Exception("Driver PostgreSQL (psycopg2) non installato")
        except Exception as e:
            raise Exception(f"Errore ispezionando PostgreSQL: {str(e)}")
    
    def _map_postgres_type(self, data_type: str, udt_name: str, geom_type: Optional[str], geom_srid: Optional[int]) -> str:
        """Mappa tipo PostgreSQL al nostro tipo standard"""
        # Prima controlla se è una geometria PostGIS
        if data_type == 'USER-DEFINED' and udt_name == 'geometry':
            if geom_type and geom_srid:
                geom_type_lower = geom_type.lower()
                type_map = {
                    'point': 'geometry:point',
                    'linestring': 'geometry:line',
                    'polygon': 'geometry:polygon',
                    'multipoint': 'geometry:multipoint',
                    'multilinestring': 'geometry:multiline',
                    'multipolygon': 'geometry:multipolygon',
                }
                base_type = type_map.get(geom_type_lower, 'geometry')
                return f"{base_type}:{geom_srid}"
            return 'geometry'
        
        # Altrimenti mappa con la tabella standard
        data_type_lower = data_type.lower()
        if data_type_lower in self.POSTGRES_TYPE_MAP:
            return self.POSTGRES_TYPE_MAP[data_type_lower]
        
        return 'str'  # Default
    
    # ==================== MS SQL SERVER ====================
    
    def _inspect_mssql(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """Ispeziona MS SQL Server con supporto Spatial"""
        try:
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
                    connection = pyodbc.connect(conn_string, timeout=10)
                    break
                except pyodbc.InterfaceError:
                    continue
            
            if connection is None:
                raise Exception("Nessun driver ODBC per SQL Server trovato")
            
            cursor = connection.cursor()
            
            # Query con tipi
            query = """
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
            
            cursor.execute(query)
            rows = cursor.fetchall()
            
            cursor.close()
            connection.close()
            
            # Raggruppa per tabella con tipi
            tables: Dict[str, List[ColumnInfo]] = {}
            for table_name, column_name, data_type in rows:
                if table_name not in tables:
                    tables[table_name] = []
                
                col_type = self._map_mssql_type(data_type)
                
                tables[table_name].append({
                    "name": column_name,
                    "type": col_type
                })
            
            return tables
        
        except ImportError:
            raise Exception("Driver MS SQL (pyodbc) non installato")
        except Exception as e:
            raise Exception(f"Errore ispezionando MS SQL Server: {str(e)}")
    
    def _map_mssql_type(self, data_type: str) -> str:
        """Mappa tipo MS SQL Server al nostro tipo standard"""
        data_type_lower = data_type.lower()
        
        if data_type_lower in self.MSSQL_TYPE_MAP:
            return self.MSSQL_TYPE_MAP[data_type_lower]
        
        return 'str'
    
    # ==================== MYSQL ====================
    
    def _inspect_mysql(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """Ispeziona MySQL/MariaDB con supporto Spatial"""
        try:
            import pymysql
            
            connection = pymysql.connect(
                host=conn.host,
                port=conn.port,
                database=conn.database,
                user=conn.username,
                password=conn.password,
                connect_timeout=10
            )
            
            cursor = connection.cursor()
            
            # Detecta se è MariaDB o MySQL
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()[0].lower()
            is_mariadb = 'mariadb' in version
            
            rows = []
            has_srid = False
            
            if is_mariadb:
                # MariaDB: query semplice senza JOIN a GEOMETRY_COLUMNS
                query = """
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
                cursor.execute(query)
                rows = cursor.fetchall()
                has_srid = False
            else:
                # MySQL 8+: usa ST_GEOMETRY_COLUMNS
                try:
                    query = """
                        SELECT 
                            TABLE_NAME, 
                            COLUMN_NAME,
                            DATA_TYPE,
                            COLUMN_TYPE,
                            SRS_ID
                        FROM INFORMATION_SCHEMA.COLUMNS
                        LEFT JOIN INFORMATION_SCHEMA.ST_GEOMETRY_COLUMNS 
                            ON COLUMNS.TABLE_SCHEMA = ST_GEOMETRY_COLUMNS.TABLE_SCHEMA
                            AND COLUMNS.TABLE_NAME = ST_GEOMETRY_COLUMNS.TABLE_NAME
                            AND COLUMNS.COLUMN_NAME = ST_GEOMETRY_COLUMNS.COLUMN_NAME
                        WHERE COLUMNS.TABLE_SCHEMA = DATABASE()
                        ORDER BY COLUMNS.TABLE_NAME, COLUMNS.ORDINAL_POSITION;
                    """
                    cursor.execute(query)
                    rows = cursor.fetchall()
                    has_srid = True
                except Exception:
                    # Fallback per MySQL < 8
                    query = """
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
                    cursor.execute(query)
                    rows = cursor.fetchall()
            
            cursor.close()
            connection.close()
            
            # Raggruppa per tabella con tipi
            tables: Dict[str, List[ColumnInfo]] = {}
            for row in rows:
                table_name = row[0]
                column_name = row[1]
                data_type = row[2]
                column_type = row[3]
                srs_id = row[4] if has_srid else None
                
                if table_name not in tables:
                    tables[table_name] = []
                
                col_type = self._map_mysql_type(data_type, column_type, srs_id)
                
                tables[table_name].append({
                    "name": column_name,
                    "type": col_type
                })
            
            return tables
        
        except ImportError:
            raise Exception("Driver MySQL (pymysql) non installato. Installa con: pip install pymysql")
        except Exception as e:
            raise Exception(f"Errore ispezionando MySQL/MariaDB: {str(e)}")
    
    def _map_mysql_type(self, data_type: str, column_type: str, srs_id: Optional[int] = None) -> str:
        """Mappa tipo MySQL al nostro tipo standard"""
        data_type_lower = data_type.lower()
        column_type_lower = column_type.lower() if column_type else ''
        
        # Caso speciale: tinyint(1) è spesso usato come boolean
        if data_type_lower == 'tinyint' and '(1)' in column_type_lower:
            return 'bool'
        
        # Geometrie con SRID
        geometry_types = ['geometry', 'point', 'linestring', 'polygon', 
                         'multipoint', 'multilinestring', 'multipolygon', 'geometrycollection']
        if data_type_lower in geometry_types:
            srid_suffix = f":{srs_id}" if srs_id and srs_id > 0 else ""
            base_type = self.MYSQL_TYPE_MAP.get(data_type_lower, 'geometry')
            return f"{base_type}{srid_suffix}"
        
        if data_type_lower in self.MYSQL_TYPE_MAP:
            return self.MYSQL_TYPE_MAP[data_type_lower]
        
        return 'str'
    
    # ==================== ORACLE ====================
    
    def _inspect_oracle(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """Ispeziona Oracle con supporto Spatial"""
        # Prova prima oracledb (nuovo), poi cx_Oracle (legacy)
        try:
            import oracledb
            
            connection = oracledb.connect(
                user=conn.username,
                password=conn.password,
                dsn=f"{conn.host}:{conn.port}/{conn.database}"
            )
            
            cursor = connection.cursor()
            
            # Query con tipi - Oracle usa USER_TAB_COLUMNS
            query = """
                SELECT 
                    TABLE_NAME, 
                    COLUMN_NAME,
                    DATA_TYPE,
                    DATA_PRECISION,
                    DATA_SCALE
                FROM USER_TAB_COLUMNS
                ORDER BY TABLE_NAME, COLUMN_ID
            """
            
            cursor.execute(query)
            rows = cursor.fetchall()
            
            cursor.close()
            connection.close()
            
            # Raggruppa per tabella con tipi
            tables: Dict[str, List[ColumnInfo]] = {}
            for table_name, column_name, data_type, precision, scale in rows:
                if table_name not in tables:
                    tables[table_name] = []
                
                col_type = self._map_oracle_type(data_type, precision, scale)
                
                tables[table_name].append({
                    "name": column_name,
                    "type": col_type
                })
            
            return tables
        
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
                
                query = """
                    SELECT 
                        TABLE_NAME, 
                        COLUMN_NAME,
                        DATA_TYPE,
                        DATA_PRECISION,
                        DATA_SCALE
                    FROM USER_TAB_COLUMNS
                    ORDER BY TABLE_NAME, COLUMN_ID
                """
                
                cursor.execute(query)
                rows = cursor.fetchall()
                
                cursor.close()
                connection.close()
                
                # Raggruppa per tabella con tipi
                tables: Dict[str, List[ColumnInfo]] = {}
                for table_name, column_name, data_type, precision, scale in rows:
                    if table_name not in tables:
                        tables[table_name] = []
                    
                    col_type = self._map_oracle_type(data_type, precision, scale)
                    
                    tables[table_name].append({
                        "name": column_name,
                        "type": col_type
                    })
                
                return tables
            
            except ImportError:
                raise Exception("Driver Oracle non installato. Installa con: pip install oracledb")
            except Exception as e:
                raise Exception(f"Errore ispezionando Oracle: {str(e)}")
        
        except Exception as e:
            raise Exception(f"Errore ispezionando Oracle: {str(e)}")
    
    def _map_oracle_type(self, data_type: str, precision: Optional[int], scale: Optional[int]) -> str:
        """Mappa tipo Oracle al nostro tipo standard"""
        data_type_upper = data_type.upper()
        
        # Caso speciale: NUMBER senza decimali è integer
        if data_type_upper == 'NUMBER':
            if scale is not None and scale == 0:
                return 'int'
            return 'real'
        
        # Caso speciale: NUMBER(1) spesso usato come boolean
        if data_type_upper == 'NUMBER' and precision == 1:
            return 'bool'
        
        data_type_lower = data_type.lower()
        if data_type_lower in self.ORACLE_TYPE_MAP:
            return self.ORACLE_TYPE_MAP[data_type_lower]
        
        return 'str'
    
    # ==================== SQLITE ====================
    
    def _inspect_sqlite(self, conn: DatabaseConnection) -> Dict[str, List[ColumnInfo]]:
        """Ispeziona SQLite"""
        try:
            import sqlite3
            
            connection = sqlite3.connect(conn.database, timeout=10)
            cursor = connection.cursor()
            
            # Query per ottenere tabelle
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            table_names = [row[0] for row in cursor.fetchall()]
            
            # Per ogni tabella, ottieni colonne con tipi
            tables: Dict[str, List[ColumnInfo]] = {}
            for table_name in table_names:
                cursor.execute(f"PRAGMA table_info({table_name});")
                # PRAGMA table_info restituisce: cid, name, type, notnull, dflt_value, pk
                columns = []
                for row in cursor.fetchall():
                    col_name = row[1]
                    col_type_raw = row[2].lower() if row[2] else 'text'
                    
                    # Mappa il tipo
                    col_type = self._map_sqlite_type(col_type_raw)
                    
                    columns.append({
                        "name": col_name,
                        "type": col_type
                    })
                
                tables[table_name] = columns
            
            cursor.close()
            connection.close()
            
            return tables
        
        except Exception as e:
            raise Exception(f"Errore ispezionando SQLite: {str(e)}")
    
    def _map_sqlite_type(self, type_str: str) -> str:
        """Mappa tipo SQLite al nostro tipo standard"""
        # SQLite ha tipizzazione dinamica, i tipi sono suggerimenti
        type_lower = type_str.lower().strip()
        
        # Rimuovi lunghezze tipo varchar(255) -> varchar
        if '(' in type_lower:
            type_lower = type_lower.split('(')[0].strip()
        
        if type_lower in self.SQLITE_TYPE_MAP:
            return self.SQLITE_TYPE_MAP[type_lower]
        
        # Euristica per tipi non standard
        if 'int' in type_lower:
            return 'int'
        elif 'real' in type_lower or 'float' in type_lower or 'double' in type_lower:
            return 'real'
        elif 'bool' in type_lower:
            return 'bool'
        elif 'date' in type_lower or 'time' in type_lower:
            return 'date'
        
        return 'str'