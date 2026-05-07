"""
Generatore DDL multi-database per il modulo CARICA.

Keeps database loading behavior isolated from orchestration callers.
"""

from typing import Any, Dict, List, Optional, Tuple

from app.core.carica.models import ColumnMapping


# MAPPING TIPI PANDAS → SQL PER OGNI DATABASE

# Mappa dtype Pandas/Python → tipo SQL
# I tipi geometrici sono gestiti separatamente tramite ColumnMapping.is_geometry
PANDAS_TO_SQL = {
    "postgres": {
        "int64": "BIGINT",
        "int32": "INTEGER",
        "int16": "SMALLINT",
        "int8": "SMALLINT",
        "float64": "DOUBLE PRECISION",
        "float32": "REAL",
        "bool": "BOOLEAN",
        "object": "TEXT",
        "str": "TEXT",
        "string": "TEXT",
        "datetime64[ns]": "TIMESTAMP",
        "datetime64": "TIMESTAMP",
        "date": "DATE",
        "category": "TEXT",
    },
    "oracle": {
        "int64": "NUMBER(19)",
        "int32": "NUMBER(10)",
        "int16": "NUMBER(5)",
        "int8": "NUMBER(3)",
        "float64": "NUMBER",
        "float32": "FLOAT",
        "bool": "NUMBER(1)",
        "object": "CLOB",
        "str": "VARCHAR2(4000)",
        "string": "VARCHAR2(4000)",
        "datetime64[ns]": "TIMESTAMP",
        "datetime64": "TIMESTAMP",
        "date": "DATE",
        "category": "VARCHAR2(4000)",
    },
    "mssql": {
        "int64": "BIGINT",
        "int32": "INT",
        "int16": "SMALLINT",
        "int8": "TINYINT",
        "float64": "FLOAT",
        "float32": "REAL",
        "bool": "BIT",
        "object": "NVARCHAR(MAX)",
        "str": "NVARCHAR(MAX)",
        "string": "NVARCHAR(MAX)",
        "datetime64[ns]": "DATETIME2",
        "datetime64": "DATETIME2",
        "date": "DATE",
        "category": "NVARCHAR(MAX)",
    },
    "mysql": {
        "int64": "BIGINT",
        "int32": "INT",
        "int16": "SMALLINT",
        "int8": "TINYINT",
        "float64": "DOUBLE",
        "float32": "FLOAT",
        "bool": "TINYINT(1)",
        "object": "LONGTEXT",
        "str": "TEXT",
        "string": "TEXT",
        "datetime64[ns]": "DATETIME",
        "datetime64": "DATETIME",
        "date": "DATE",
        "category": "TEXT",
    },
}

# Alias per MariaDB (stessa sintassi MySQL)
PANDAS_TO_SQL["mariadb"] = PANDAS_TO_SQL["mysql"]


# MAPPING TIPI PLENORA INTERNI → SQL

# Quando il tipo viene dal canvas (tipi interni PLENORA), usiamo questa mappa
URBY_TO_SQL = {
    "postgres": {
        "int": "BIGINT",
        "real": "DOUBLE PRECISION",
        "str": "TEXT",
        "bool": "BOOLEAN",
        "date": "TIMESTAMP",
    },
    "oracle": {
        "int": "NUMBER(19)",
        "real": "NUMBER",
        "str": "VARCHAR2(4000)",
        "bool": "NUMBER(1)",
        "date": "TIMESTAMP",
    },
    "mssql": {
        "int": "BIGINT",
        "real": "FLOAT",
        "str": "NVARCHAR(MAX)",
        "bool": "BIT",
        "date": "DATETIME2",
    },
    "mysql": {
        "int": "BIGINT",
        "real": "DOUBLE",
        "str": "TEXT",
        "bool": "TINYINT(1)",
        "date": "DATETIME",
    },
}
URBY_TO_SQL["mariadb"] = URBY_TO_SQL["mysql"]


class DDLGenerator:
    """Generatore DDL multi-database.

    Uso:
        gen = DDLGenerator("postgres")
        ddl = gen.generate_create_table(
            schema="public",
            table="immobili",
            columns=[ColumnMapping(...)],
            srid=4326
        )
    """

    def __init__(self, db_type: str):
        """
        Args:
            db_type: tipo database ("postgres", "oracle", "mssql", "mysql", "mariadb")
        """
        self.db_type = db_type.lower()
        if self.db_type not in PANDAS_TO_SQL:
            raise ValueError(f"Database type '{db_type}' non supportato. "
                           f"Supportati: {list(PANDAS_TO_SQL.keys())}")

    # QUALIFICAZIONE NOMI

    def qualified_table_name(self, schema: str, table: str) -> str:
        """Genera il nome tabella qualificato con schema.

        PostgreSQL/MSSQL: "schema"."tabella"
        Oracle: "SCHEMA"."TABELLA" (uppercase)
        MySQL/MariaDB: `schema`.`tabella` (backtick)
        """
        # B-BE-60: strip spazi da schema e table per evitare nomi SQL malformati
        table = table.strip() if table else table
        schema = schema.strip() if schema else schema
        if self.db_type in ("mysql", "mariadb"):
            if schema:
                return f"`{schema}`.`{table}`"
            return f"`{table}`"
        elif self.db_type == "oracle":
            schema_upper = schema.upper() if schema else ""
            table_upper = table.upper()
            if schema_upper:
                return f'"{schema_upper}"."{table_upper}"'
            return f'"{table_upper}"'
        else:
            # postgres, mssql
            if schema:
                return f'"{schema}"."{table}"'
            return f'"{table}"'

    def quote_column(self, name: str) -> str:
        """Quota un nome di colonna."""
        if self.db_type in ("mysql", "mariadb"):
            return f"`{name}`"
        elif self.db_type == "oracle":
            return f'"{name.upper()}"'
        else:
            return f'"{name}"'

    # RISOLUZIONE TIPI

    def resolve_column_type(self, mapping: ColumnMapping, pandas_dtype: str = "") -> str:
        """Risolve il tipo SQL per una colonna.

        Priorità:
        1. Se mapping.dest_type è già specificato dall'utente → usa quello
        2. Se mapping.is_geometry → tipo geometria specifico per DB
        3. Se mapping.dest_type è un tipo PLENORA interno → converte
        4. Altrimenti → usa pandas_dtype per la conversione

        Args:
            mapping: mappatura colonna
            pandas_dtype: dtype Pandas della colonna sorgente (fallback)

        Returns:
            Tipo SQL nativo per il database.
        """
        # Geometria
        if mapping.is_geometry:
            return self._geometry_type(mapping.srid or 4326)

        # Se il dtype reale del DataFrame è object (tipi misti: numeri + stringhe),
        # forza TEXT/VARCHAR indipendentemente dal tipo dichiarato nel mapping.
        # Caso tipico: colonna "numero" con valori 95, 100, "26A"
        dtype_str_check = str(pandas_dtype).lower().strip()
        if dtype_str_check == "object":
            type_map = PANDAS_TO_SQL.get(self.db_type, PANDAS_TO_SQL["postgres"])
            return type_map.get("object", "TEXT")

        # Tipo esplicito dall'utente (già SQL)
        if mapping.dest_type and mapping.dest_type.upper() not in (
            "INT", "REAL", "STR", "BOOL", "DATE", "GEOMETRY", ""
        ):
            return mapping.dest_type

        # Tipo PLENORA interno (dal canvas)
        base_type = mapping.dest_type.split(":")[0].lower() if mapping.dest_type else ""
        if base_type in URBY_TO_SQL.get(self.db_type, {}):
            return URBY_TO_SQL[self.db_type][base_type]

        # Fallback: pandas dtype
        dtype_str = str(pandas_dtype).lower()
        type_map = PANDAS_TO_SQL.get(self.db_type, PANDAS_TO_SQL["postgres"])

        # Prova match esatto, poi parziale
        if dtype_str in type_map:
            return type_map[dtype_str]
        for key, val in type_map.items():
            if key in dtype_str:
                return val

        # Default: TEXT/CLOB/NVARCHAR(MAX)
        return type_map.get("object", "TEXT")

    def _geometry_type(self, srid: int) -> str:
        """Genera il tipo geometria specifico per database.

        PostgreSQL (PostGIS): geometry(Geometry, 4326)
        Oracle: SDO_GEOMETRY (SRID gestito via metadata)
        SQL Server: geometry (SRID gestito nel valore)
        MySQL: GEOMETRY SRID 4326 (MySQL 8.0+)
        """
        if self.db_type == "postgres":
            return f"geometry(Geometry, {srid})"
        elif self.db_type == "oracle":
            return "SDO_GEOMETRY"
        elif self.db_type == "mssql":
            return "geometry"
        elif self.db_type in ("mysql", "mariadb"):
            return f"GEOMETRY /*!80003 SRID {srid} */"
        return "geometry"

    # GENERAZIONE CREATE TABLE

    def generate_create_table(
        self,
        schema: str,
        table: str,
        columns: List[ColumnMapping],
        pandas_dtypes: Optional[Dict[str, str]] = None,
        max_lengths: Optional[Dict[str, int]] = None,
    ) -> str:
        """Genera l'istruzione CREATE TABLE completa.

        Args:
            schema: nome schema destinazione
            table: nome tabella destinazione
            columns: lista mappature colonne
            pandas_dtypes: dizionario {col_name: pandas_dtype} per fallback tipi
            max_lengths: dizionario {col_name: max_char_length} opzionale,
                calcolato dal DataFrame sorgente (`df[col].str.len().max()`).
                Su Oracle, abilita l'auto-promozione VARCHAR2(4000) → CLOB
                per colonne stringa con contenuto > 4000 char, evitando
                `ORA-12899 value too large for column` (C5 fix 2).
                Ignorato per altri dialetti che hanno gia' tipi stringa
                illimitati (Postgres TEXT, MSSQL NVARCHAR(MAX), MySQL TEXT).

        Returns:
            Stringa SQL CREATE TABLE.
        """
        pandas_dtypes = pandas_dtypes or {}
        max_lengths = max_lengths or {}
        full_name = self.qualified_table_name(schema, table)

        col_defs = []
        for col in columns:
            if col.skip:
                continue
            quoted_name = self.quote_column(col.dest_column)
            dtype = str(pandas_dtypes.get(col.source_column, "") or pandas_dtypes.get(col.dest_column, ""))
            sql_type = self.resolve_column_type(col, dtype)

            # C5 fix 2: auto-promote Oracle VARCHAR2(4000) → CLOB se la
            # colonna supera la soglia. Controlla sia source_column che
            # dest_column per robustezza contro mapping con rename.
            if self.db_type == "oracle" and "VARCHAR2(4000)" in sql_type.upper():
                col_max = max(
                    int(max_lengths.get(col.source_column, 0) or 0),
                    int(max_lengths.get(col.dest_column, 0) or 0),
                )
                if col_max > 4000:
                    sql_type = "CLOB"

            col_defs.append(f"    {quoted_name} {sql_type}")

        cols_sql = ",\n".join(col_defs)
        return f"CREATE TABLE {full_name} (\n{cols_sql}\n)"

    # GENERAZIONE INDICI SPAZIALI

    def generate_spatial_index(
        self,
        schema: str,
        table: str,
        geometry_column: str,
        srid: int = 4326,
    ) -> List[str]:
        """Genera le istruzioni per creare l'indice spaziale.

        Ogni database ha la sua sintassi specifica. Può restituire
        più istruzioni (es. Oracle: metadata + index).

        Returns:
            Lista di istruzioni SQL da eseguire in ordine.
        """
        # B-BE-60: strip spazi da table/schema/geometry_column per evitare
        # nomi indice e tabella malformati (es. "idx_test_c01 _geometry_spatial")
        table = table.strip()
        schema = schema.strip() if schema else schema
        geometry_column = geometry_column.strip()
        full_name = self.qualified_table_name(schema, table)
        geom_col = self.quote_column(geometry_column)
        index_name = f"idx_{table}_{geometry_column}_spatial"

        if self.db_type == "postgres":
            return [
                f"CREATE INDEX {index_name} ON {full_name} USING GIST ({geom_col})"
            ]

        elif self.db_type == "oracle":
            # Oracle richiede: 1) INSERT metadata, 2) CREATE INDEX
            import re as _re
            # Validazione identificatori Oracle (solo alfanumerico + underscore)
            _ident = _re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
            schema_upper = schema.upper() if schema else "USER"
            table_upper = table.upper()
            geom_upper = geometry_column.upper()
            for _id in (table_upper, geom_upper):
                if not _ident.match(_id):
                    raise ValueError(f"Oracle identifier non valido: {_id}")
            return [
                # Metadata per Oracle Spatial
                f"""INSERT INTO USER_SDO_GEOM_METADATA (TABLE_NAME, COLUMN_NAME, DIMINFO, SRID)
VALUES (
    '{table_upper}',
    '{geom_upper}',
    SDO_DIM_ARRAY(
        SDO_DIM_ELEMENT('X', -180, 180, 0.005),
        SDO_DIM_ELEMENT('Y', -90, 90, 0.005)
    ),
    {srid}
)""",
                # Index
                f"""CREATE INDEX {index_name} ON {full_name}({geom_col})
INDEXTYPE IS MDSYS.SPATIAL_INDEX""",
            ]

        elif self.db_type == "mssql":
            return [
                f"""CREATE SPATIAL INDEX {index_name}
ON {full_name}({geom_col})
WITH (
    BOUNDING_BOX = (xmin=-180, ymin=-90, xmax=180, ymax=90)
)"""
            ]

        elif self.db_type in ("mysql", "mariadb"):
            return [
                f"CREATE SPATIAL INDEX {index_name} ON {full_name}({geom_col})"
            ]

        return []

    # GENERAZIONE DROP / TRUNCATE

    def generate_drop_table(self, schema: str, table: str) -> str:
        """Genera DROP TABLE IF EXISTS."""
        full_name = self.qualified_table_name(schema, table)

        if self.db_type == "oracle":
            # Oracle non supporta IF EXISTS prima di 23c
            table_upper = table.upper()
            schema_check = f"AND owner = '{schema.upper()}'" if schema else ""
            return f"""
BEGIN
    EXECUTE IMMEDIATE 'DROP TABLE {full_name} CASCADE CONSTRAINTS';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -942 THEN RAISE; END IF;
END;"""
        else:
            return f"DROP TABLE IF EXISTS {full_name}"

    # Alias semantico: il writer per stage+swap vuole sottolineare
    # "drop defensivo di una shadow eventualmente orfana", non un
    # drop generico. L'implementazione e' la stessa.
    def generate_drop_if_exists(self, schema: str, table: str) -> str:
        return self.generate_drop_table(schema, table)

    # GENERAZIONE STAGE+SWAP (C5 fix 3)

    def generate_create_like(
        self, schema: str, src_table: str, stg_table: str
    ) -> str:
        """Crea una shadow table vuota con struttura derivata dal target.

        Preservazione per dialetto:
        - Postgres: LIKE ... INCLUDING ALL → colonne + indexes +
          constraints + defaults + storage (completo).
        - MySQL: LIKE nativo → colonne + indexes (NO FK — limitazione
          MySQL documentata).
        - Oracle: CREATE AS SELECT WHERE 1=0 → solo colonne + NOT NULL
          derivati. Indexes/constraints NON preservati (limitazione
          documentata nel flow stage+swap).
        - MSSQL: SELECT INTO WHERE 1=0 → solo colonne (stessa
          limitazione Oracle).

        Per il caso d'uso TRUNCATE_INSERT + ALL_OR_NOTHING la
        limitazione Oracle/MSSQL e' accettabile: l'operatore sceglie
        stage+swap consapevolmente per tabelle grandi, dove il costo
        di ricostruire indexes post-swap e' comunque minore del
        rischio UNDO saturation.
        """
        src_full = self.qualified_table_name(schema, src_table)
        stg_full = self.qualified_table_name(schema, stg_table)

        if self.db_type == "postgres":
            return f"CREATE TABLE {stg_full} (LIKE {src_full} INCLUDING ALL)"

        if self.db_type in ("mysql", "mariadb"):
            return f"CREATE TABLE {stg_full} LIKE {src_full}"

        if self.db_type == "oracle":
            return f"CREATE TABLE {stg_full} AS SELECT * FROM {src_full} WHERE 1=0"

        if self.db_type == "mssql":
            return f"SELECT * INTO {stg_full} FROM {src_full} WHERE 1=0"

        raise ValueError(
            f"generate_create_like non supportato per db_type={self.db_type}"
        )

    def generate_swap_rename(
        self, schema: str, src_table: str, stg_table: str
    ) -> List[str]:
        """Statements per 3-way swap: src→backup, stg→src.

        Il drop del backup e' responsabilita' del writer (post-swap).
        La shadow `{src}__bak` e' l'unica "orfana" possibile se il
        processo crasha a meta'; il writer la elimina defensivamente
        all'inizio di ogni stage+swap via generate_drop_if_exists.

        Atomicita' per dialetto:
        - Postgres: 2 ALTER TABLE RENAME. Caller li wrappa in
          BEGIN/COMMIT → atomico.
        - MySQL: UNA RENAME TABLE con 2 coppie → atomica by design.
        - Oracle: 2 ALTER TABLE RENAME. Ogni DDL e' auto-commit →
          finestra di inconsistenza sub-secondo (documentata).
        - MSSQL: 2 sp_rename. Caller wrappa in BEGIN TRAN → atomico.
        """
        src_full = self.qualified_table_name(schema, src_table)
        stg_full = self.qualified_table_name(schema, stg_table)
        backup_name = f"{src_table}__bak"

        if self.db_type == "postgres":
            backup_id = f'"{backup_name}"'
            src_id = f'"{src_table}"'
            return [
                f'ALTER TABLE {src_full} RENAME TO {backup_id}',
                f'ALTER TABLE {stg_full} RENAME TO {src_id}',
            ]

        if self.db_type in ("mysql", "mariadb"):
            backup_full = self.qualified_table_name(schema, backup_name)
            return [
                f"RENAME TABLE {src_full} TO {backup_full}, "
                f"{stg_full} TO {src_full}"
            ]

        if self.db_type == "oracle":
            backup_id = f'"{backup_name.upper()}"'
            src_id = f'"{src_table.upper()}"'
            return [
                f"ALTER TABLE {src_full} RENAME TO {backup_id}",
                f"ALTER TABLE {stg_full} RENAME TO {src_id}",
            ]

        if self.db_type == "mssql":
            # sp_rename: il nuovo nome e' unqualified (stesso schema)
            src_qualified_str = f"{schema}.{src_table}" if schema else src_table
            stg_qualified_str = f"{schema}.{stg_table}" if schema else stg_table
            return [
                f"EXEC sp_rename '{src_qualified_str}', '{backup_name}'",
                f"EXEC sp_rename '{stg_qualified_str}', '{src_table}'",
            ]

        raise ValueError(
            f"generate_swap_rename non supportato per db_type={self.db_type}"
        )

    def generate_truncate(self, schema: str, table: str) -> str:
        """Genera SQL per svuotare la tabella.

        Oracle E MySQL: usano `DELETE FROM` (DML, transazionale). Su
        entrambi `TRUNCATE TABLE` e' DDL con implicit commit: se un
        INSERT successivo fallisce il rollback NON puo' ripristinare
        le righe cancellate → perdita dati deterministica nel flow
        truncate_insert + ALL_OR_NOTHING. Con DELETE FROM la
        transazione copre sia cancellazione che inserimento.
        Performance inferiore su tabelle grandi e' un trade-off
        accettato per safety (C5 fix 1, esteso a MySQL in fix 1b).

        Postgres e SQL Server: TRUNCATE TABLE e' transazionale nella
        stessa connection, niente problema di auto-commit.
        """
        full_name = self.qualified_table_name(schema, table)

        # Dialetti con TRUNCATE TABLE = DDL implicit-commit.
        # Oracle: tutti i TRUNCATE sono DDL auto-commit.
        # MySQL:  TRUNCATE su InnoDB = DDL con implicit commit
        #         (vedi MySQL ref "TRUNCATE TABLE is classified as a
        #         DDL statement rather than a DML statement").
        if self.db_type in ("oracle", "mysql"):
            return f"DELETE FROM {full_name}"

        return f"TRUNCATE TABLE {full_name}"

    # GENERAZIONE INSERT

    def generate_insert(
        self,
        schema: str,
        table: str,
        columns: List[ColumnMapping],
    ) -> Tuple[str, List[str]]:
        """Genera l'istruzione INSERT INTO con parametri preparati.

        Returns:
            Tupla (sql_template, param_names) dove sql_template contiene
            placeholder (:param_name) per SQLAlchemy text().
        """
        full_name = self.qualified_table_name(schema, table)

        active_cols = [c for c in columns if not c.skip]
        col_names = [self.quote_column(c.dest_column) for c in active_cols]
        param_names = [c.dest_column for c in active_cols]

        placeholders = []
        for col in active_cols:
            if col.is_geometry:
                placeholders.append(
                    self._geometry_placeholder(col.dest_column, col.srid or 4326)
                )
            else:
                placeholders.append(f":{col.dest_column}")

        cols_sql = ", ".join(col_names)
        vals_sql = ", ".join(placeholders)

        sql = f"INSERT INTO {full_name} ({cols_sql}) VALUES ({vals_sql})"
        return sql, param_names

    def _geometry_placeholder(self, param_name: str, srid: int) -> str:
        """Genera il placeholder per un valore geometria.

        Converte WKT → tipo geometria nativo del database.
        Il valore passato sarà sempre WKT (es. "POINT(12.5 41.9)").

        PostgreSQL: ST_GeomFromText(:geom, 4326)
        Oracle: SDO_GEOMETRY(:geom, 4326)
        SQL Server: geometry::STGeomFromText(:geom, 4326)
        MySQL: ST_GeomFromText(:geom, 4326)
        """
        if self.db_type == "postgres":
            return f"ST_GeomFromText(:{param_name}, {srid})"
        elif self.db_type == "oracle":
            return f"SDO_GEOMETRY(:{param_name}, {srid})"
        elif self.db_type == "mssql":
            return f"geometry::STGeomFromText(:{param_name}, {srid})"
        elif self.db_type in ("mysql", "mariadb"):
            return f"ST_GeomFromText(:{param_name}, {srid})"
        return f":{param_name}"

    # GENERAZIONE UPSERT (multi-database)

    def generate_upsert(
        self,
        schema: str,
        table: str,
        columns: List[ColumnMapping],
        match_columns: List[str],
        update_columns: Optional[List[str]] = None,
    ) -> Tuple[str, List[str]]:
        """Genera l'istruzione UPSERT specifica per database.

        PostgreSQL: INSERT ... ON CONFLICT ... DO UPDATE
        Oracle: MERGE INTO ... USING DUAL ON ... WHEN MATCHED/NOT MATCHED
        SQL Server: MERGE INTO ... USING (VALUES ...) ON ... WHEN MATCHED/NOT MATCHED
        MySQL: INSERT ... ON DUPLICATE KEY UPDATE

        Args:
            schema: schema destinazione
            table: tabella destinazione
            columns: mappature colonne
            match_columns: colonne per il match (conflict/merge key)
            update_columns: colonne da aggiornare (vuoto = tutte non-match)

        Returns:
            Tupla (sql_template, param_names) con placeholder SQLAlchemy.
        """
        full_name = self.qualified_table_name(schema, table)
        active_cols = [c for c in columns if not c.skip]
        col_names = [self.quote_column(c.dest_column) for c in active_cols]
        param_names = [c.dest_column for c in active_cols]

        # Colonne da aggiornare (tutte tranne match, o lista esplicita)
        if update_columns:
            update_cols = [c for c in active_cols
                         if c.dest_column in update_columns]
        else:
            update_cols = [c for c in active_cols
                         if c.dest_column not in match_columns]

        if self.db_type == "postgres":
            return self._upsert_postgres(
                full_name, active_cols, match_columns, update_cols, param_names
            )
        elif self.db_type == "oracle":
            return self._upsert_oracle(
                full_name, active_cols, match_columns, update_cols, param_names
            )
        elif self.db_type == "mssql":
            return self._upsert_mssql(
                full_name, active_cols, match_columns, update_cols, param_names
            )
        elif self.db_type in ("mysql", "mariadb"):
            return self._upsert_mysql(
                full_name, active_cols, match_columns, update_cols, param_names
            )
        else:
            raise ValueError(f"UPSERT non supportato per {self.db_type}")

    def _upsert_postgres(self, full_name, cols, match_cols, update_cols, params):
        """INSERT ... ON CONFLICT (match) DO UPDATE SET ..."""
        col_names = ", ".join(self.quote_column(c.dest_column) for c in cols)
        placeholders = []
        for c in cols:
            if c.is_geometry:
                placeholders.append(self._geometry_placeholder(c.dest_column, c.srid or 4326))
            else:
                placeholders.append(f":{c.dest_column}")
        vals = ", ".join(placeholders)

        conflict_keys = ", ".join(self.quote_column(m) for m in match_cols)
        updates = ", ".join(
            f"{self.quote_column(c.dest_column)} = EXCLUDED.{self.quote_column(c.dest_column)}"
            for c in update_cols
        )

        sql = (
            f"INSERT INTO {full_name} ({col_names}) VALUES ({vals}) "
            f"ON CONFLICT ({conflict_keys}) DO UPDATE SET {updates}"
        )
        return sql, params

    def _upsert_oracle(self, full_name, cols, match_cols, update_cols, params):
        """MERGE INTO ... USING DUAL ON ... WHEN MATCHED/NOT MATCHED."""
        match_cond = " AND ".join(
            f"dest.{self.quote_column(m)} = :{m}" for m in match_cols
        )
        update_set = ", ".join(
            f"dest.{self.quote_column(c.dest_column)} = :{c.dest_column}"
            for c in update_cols
        )
        insert_cols = ", ".join(self.quote_column(c.dest_column) for c in cols)
        insert_vals = []
        for c in cols:
            if c.is_geometry:
                insert_vals.append(self._geometry_placeholder(c.dest_column, c.srid or 4326))
            else:
                insert_vals.append(f":{c.dest_column}")
        insert_vals_sql = ", ".join(insert_vals)

        sql = f"""MERGE INTO {full_name} dest
USING DUAL ON ({match_cond})
WHEN MATCHED THEN UPDATE SET {update_set}
WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals_sql})"""
        return sql, params

    def _upsert_mssql(self, full_name, cols, match_cols, update_cols, params):
        """MERGE INTO ... USING (VALUES ...) ON ... WHEN MATCHED/NOT MATCHED."""
        src_cols = ", ".join(f"src.{self.quote_column(c.dest_column)}" for c in cols)
        val_placeholders = []
        for c in cols:
            if c.is_geometry:
                val_placeholders.append(self._geometry_placeholder(c.dest_column, c.srid or 4326))
            else:
                val_placeholders.append(f":{c.dest_column}")
        vals = ", ".join(val_placeholders)
        src_col_aliases = ", ".join(self.quote_column(c.dest_column) for c in cols)

        match_cond = " AND ".join(
            f"target.{self.quote_column(m)} = src.{self.quote_column(m)}"
            for m in match_cols
        )
        update_set = ", ".join(
            f"target.{self.quote_column(c.dest_column)} = src.{self.quote_column(c.dest_column)}"
            for c in update_cols
        )
        insert_cols = ", ".join(self.quote_column(c.dest_column) for c in cols)

        sql = f"""MERGE INTO {full_name} AS target
USING (SELECT {vals}) AS src({src_col_aliases})
ON ({match_cond})
WHEN MATCHED THEN UPDATE SET {update_set}
WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({src_cols});"""
        return sql, params

    def _upsert_mysql(self, full_name, cols, match_cols, update_cols, params):
        """INSERT ... ON DUPLICATE KEY UPDATE ..."""
        col_names = ", ".join(self.quote_column(c.dest_column) for c in cols)
        placeholders = []
        for c in cols:
            if c.is_geometry:
                placeholders.append(self._geometry_placeholder(c.dest_column, c.srid or 4326))
            else:
                placeholders.append(f":{c.dest_column}")
        vals = ", ".join(placeholders)

        updates = ", ".join(
            f"{self.quote_column(c.dest_column)} = VALUES({self.quote_column(c.dest_column)})"
            for c in update_cols
        )

        sql = (
            f"INSERT INTO {full_name} ({col_names}) VALUES ({vals}) "
            f"ON DUPLICATE KEY UPDATE {updates}"
        )
        return sql, params

    # GENERAZIONE UPDATE

    def generate_update(
        self,
        schema: str,
        table: str,
        update_columns: List[ColumnMapping],
        match_columns: List[str],
    ) -> Tuple[str, List[str]]:
        """Genera UPDATE ... SET ... WHERE (match).

        Args:
            schema: schema destinazione
            table: tabella destinazione
            update_columns: colonne da aggiornare
            match_columns: colonne per il WHERE

        Returns:
            Tupla (sql_template, param_names).
        """
        full_name = self.qualified_table_name(schema, table)

        set_parts = []
        params = []
        for col in update_columns:
            if col.is_geometry:
                set_parts.append(
                    f"{self.quote_column(col.dest_column)} = "
                    f"{self._geometry_placeholder(col.dest_column, col.srid or 4326)}"
                )
            else:
                set_parts.append(
                    f"{self.quote_column(col.dest_column)} = :{col.dest_column}"
                )
            params.append(col.dest_column)

        where_parts = []
        for m in match_columns:
            where_parts.append(f"{self.quote_column(m)} = :match_{m}")
            params.append(f"match_{m}")

        set_sql = ", ".join(set_parts)
        where_sql = " AND ".join(where_parts)

        sql = f"UPDATE {full_name} SET {set_sql} WHERE {where_sql}"
        return sql, params

    # HELPER: LISTA SCHEMA E TABELLE

    def query_list_schemas(self) -> str:
        """Genera la query per elencare gli schema disponibili."""
        if self.db_type == "postgres":
            return """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                ORDER BY schema_name
            """
        elif self.db_type == "oracle":
            return "SELECT username FROM all_users ORDER BY username"
        elif self.db_type == "mssql":
            return """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest')
                ORDER BY schema_name
            """
        elif self.db_type in ("mysql", "mariadb"):
            return "SHOW DATABASES"
        return ""

    def query_list_tables(self, schema: str) -> tuple:
        """Genera query parametrizzata per elencare le tabelle.

        Returns: (sql_con_placeholder, params_dict)

        SECURITY: schema viene dal frontend → PARAMETRIZZATO, non inline.
        Prima: f-string con '{schema}' → SQL injection.
        """
        if self.db_type == "postgres":
            return ("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = :schema
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """, {"schema": schema})
        elif self.db_type == "oracle":
            return ("""
                SELECT table_name
                FROM all_tables
                WHERE owner = :schema
                ORDER BY table_name
            """, {"schema": schema.upper()})
        elif self.db_type == "mssql":
            return ("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = :schema
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """, {"schema": schema})
        elif self.db_type in ("mysql", "mariadb"):
            # MySQL SHOW TABLES non supporta parametri bind.
            # Validazione strict del nome schema (solo alfanumerici + underscore).
            import re
            if not re.match(r'^[a-zA-Z0-9_]+$', schema):
                raise ValueError(f"Nome schema non valido per MySQL: {schema}")
            return (f"SHOW TABLES FROM `{schema}`", {})
        return ("SELECT NULL WHERE 1=0", {})

    def query_table_exists(self, schema: str, table: str) -> tuple:
        """Genera query parametrizzata per verificare se una tabella esiste.

        Returns: (sql, params)
        """
        if self.db_type == "postgres":
            return ("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = :schema AND table_name = :table
            """, {"schema": schema, "table": table})
        elif self.db_type == "oracle":
            return ("""
                SELECT COUNT(*) FROM all_tables
                WHERE owner = :schema AND table_name = :table
            """, {"schema": schema.upper(), "table": table.upper()})
        elif self.db_type == "mssql":
            return ("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = :schema AND table_name = :table
            """, {"schema": schema, "table": table})
        elif self.db_type in ("mysql", "mariadb"):
            return ("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = :schema AND table_name = :table
            """, {"schema": schema, "table": table})
        return ("SELECT 0", {})

    def query_table_columns(self, schema: str, table: str) -> tuple:
        """Genera query parametrizzata per elencare le colonne.

        Returns: (sql, params)
        """
        if self.db_type == "postgres":
            return ("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = :schema AND table_name = :table
                ORDER BY ordinal_position
            """, {"schema": schema, "table": table})
        elif self.db_type == "oracle":
            return ("""
                SELECT column_name, data_type
                FROM all_tab_columns
                WHERE owner = :schema AND table_name = :table
                ORDER BY column_id
            """, {"schema": schema.upper(), "table": table.upper()})
        elif self.db_type == "mssql":
            return ("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = :schema AND table_name = :table
                ORDER BY ordinal_position
            """, {"schema": schema, "table": table})
        elif self.db_type in ("mysql", "mariadb"):
            return ("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = :schema AND table_name = :table
                ORDER BY ordinal_position
            """, {"schema": schema, "table": table})
        return ("SELECT NULL WHERE 1=0", {})
