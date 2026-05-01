"""
Esecutore di query SQL unificato per PLENORA-ETL.

Gestisce l'esecuzione di query su tutti i database supportati tramite
SQLAlchemy come layer di astrazione unificato.

Architettura:
    - SQLAlchemy text() per parametri preparati (prevenzione SQL injection)
    - Dependency Injection per testabilità:
        * EngineFactory: creazione engine SQLAlchemy (mockabile)
        * SaveFunction: salvataggio DataFrame su file (mockabile)
        * Logger: logging (NullLogger per test silenziosi)
    - execute_raw(): metodo unificato per preview e execute
    - execute_query(): esecuzione completa con salvataggio file

Gestione database-specifica:
    - Oracle: ALTER SESSION per cursori SDO + normalizzazione nomi colonne lowercase
    - MSSQL: fallback automatico da ODBC Driver 18 a Driver 17
    - Oracle: disabilitazione fetch LOB per conversione CLOB/BLOB automatica

Sicurezza:
    Tutte le query usano parametri preparati (SQLAlchemy text() con :param_name)
    per prevenire SQL injection. I valori utente non vengono MAI concatenati
    direttamente nelle stringhe SQL.

Uso:
    from app.db.query_executor import QueryExecutor, create_executor

    # Produzione (con DI da main.py)
    executor = QueryExecutor(engine_factory=factory, save_function=save_fn, logger=logger)

    # Test (con mock)
    executor = QueryExecutor(engine_factory=MockFactory(), logger=NullLogger())

    # Esecuzione query
    righe, colonne = executor.execute_raw(connessione, sql, params)
"""

import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Protocol
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Result store — salva blob nel DB e usa temp files
from app.utils.result_store import get_temp_output_path, save_result_blob


# =============================================================================
# DEFINIZIONI TIPI
# =============================================================================

@dataclass
class DatabaseConnection:
    """
    Configurazione minima per una connessione database.

    Versione leggera usata dal QueryExecutor (senza metadati come nome,
    descrizione, date). I dati completi sono nel modello Pydantic in models.py.
    """
    type: str       # postgres, mssql, mysql, oracle, sqlite
    host: str
    port: int
    database: str
    username: str
    password: str


@dataclass
class QueryResult:
    """
    Risultato di un'esecuzione query.

    Contiene sia il risultato di successo (righe, colonne, timing,
    `result_id` del result persistito) che quello di errore (messaggio).

    `result_id` e' il riferimento canonico al result persistito via
    `save_result_blob`. Lo storage fisico (DB BYTEA o S3) e' trasparente:
    il reader risolve dalla row `results.storage_key` — se presente
    scarica da S3, altrimenti legge BYTEA. I consumer downstream
    (`/api/tasks/{id}/download`, `PipelineArtifact.artifact_kind`) lo
    usano per accedere al result invece di tentare `output_path` che e'
    stale (il file temp viene cancellato da `save_result_blob` nel
    finally success).
    """
    success: bool
    message: str
    rows_count: int = 0
    columns: List[str] = field(default_factory=list)
    output_path: str = ""
    project_id: str = ""
    timings: Dict[str, float] = field(default_factory=dict)
    result_id: Optional[str] = None


# =============================================================================
# PROTOCOLLI PER DEPENDENCY INJECTION
# =============================================================================

class EngineFactoryProtocol(Protocol):
    """Protocollo per la factory di SQLAlchemy engines — iniettabile per test."""
    def create_engine(self, conn: DatabaseConnection) -> Engine: ...


class LoggerProtocol(Protocol):
    """Protocollo per il logger — iniettabile per test silenziosi."""
    def info(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...
    def debug(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...


class SaveFunctionProtocol(Protocol):
    """Protocollo per la funzione di salvataggio DataFrame — iniettabile per test."""
    def __call__(
        self,
        df: pd.DataFrame,
        output_path: Path,
        format: str,
        geom_column: Optional[str] = None
    ) -> Path: ...


class NullLogger:
    """Logger silenzioso per test — non produce output."""
    def info(self, msg: str) -> None: pass
    def error(self, msg: str) -> None: pass
    def debug(self, msg: str) -> None: pass
    def warning(self, msg: str) -> None: pass


# =============================================================================
# ENGINE FACTORY (default)
# =============================================================================

class DefaultEngineFactory:
    """
    Factory di default per creare SQLAlchemy engines con connection pooling.

    Costruisce la connection string appropriata per ogni tipo di database
    e gestisce configurazioni specifiche:
        - Oracle: disabilita fetch LOB automaticamente (CLOB/BLOB → str/bytes)
        - MSSQL: fallback automatico da ODBC Driver 18 a Driver 17
        - Tutti: timeout di connessione configurati per tipo

    Performance:
        Mantiene un pool di engine cached per connection key (type:host:port:db:user).
        Evita di creare e distruggere un engine per ogni query.
    """

    SUPPORTED_TYPES = ("postgres", "postgresql", "mysql", "mssql", "oracle", "sqlite")

    _MAX_ENGINES = 50

    def __init__(self):
        self._engine_cache: Dict[str, Engine] = {}

    def _connection_key(self, conn: DatabaseConnection) -> str:
        """Chiave univoca per identificare una connessione nel pool."""
        return f"{conn.type}:{conn.host}:{conn.port}:{conn.database}:{conn.username}"

    def create_engine(self, conn: DatabaseConnection) -> Engine:
        """
        Restituisce un engine pooled per la connessione specificata.

        Se esiste già un engine per questa connessione, lo riutilizza.
        Altrimenti ne crea uno nuovo con pool_pre_ping=True per verificare
        che le connessioni nel pool siano ancora valide.

        Args:
            conn: Configurazione della connessione database.

        Returns:
            Engine SQLAlchemy pronto per l'uso.

        Raises:
            ValueError: Se il tipo di database non è supportato.
        """
        if conn.type not in self.SUPPORTED_TYPES:
            raise ValueError(f"Tipo database '{conn.type}' non supportato")

        cache_key = self._connection_key(conn)

        # Restituisci engine cached se disponibile
        if cache_key in self._engine_cache:
            return self._engine_cache[cache_key]

        # Evict oldest engine se cache piena
        if len(self._engine_cache) >= self._MAX_ENGINES:
            oldest_key = next(iter(self._engine_cache))
            try:
                self._engine_cache.pop(oldest_key).dispose()
            except Exception as e:
                logger.debug(
                    "dispose() su engine evicted (key=%s) fallito, ignoro: %s: %s",
                    oldest_key,
                    type(e).__name__,
                    e,
                )

        # Oracle: configurazione specifica del driver
        if conn.type == "oracle":
            self._configura_oracle()

        conn_str = self._costruisci_connection_string(conn)
        connect_args = self._ottieni_connect_args(conn)

        # MSSQL: tentativo con Driver 18, fallback a Driver 17
        if conn.type == "mssql":
            engine = self._crea_engine_mssql(conn_str, connect_args)
        else:
            engine = create_engine(
                conn_str, connect_args=connect_args,
                pool_pre_ping=True, pool_size=5, max_overflow=10
            )

        self._engine_cache[cache_key] = engine
        return engine

    def dispose_all(self):
        """Chiude tutti gli engine nel pool. Da chiamare allo shutdown."""
        for engine in self._engine_cache.values():
            try:
                engine.dispose()
            except Exception as e:
                logger.debug(
                    "dispose() su engine in shutdown fallito, ignoro: %s: %s",
                    type(e).__name__,
                    e,
                )
        self._engine_cache.clear()

    def _configura_oracle(self):
        """
        Configura il driver Oracle per disabilitare il fetch automatico dei LOB.

        Senza questa configurazione, le colonne CLOB e BLOB restituirebbero
        oggetti LOB che richiedono una lettura esplicita, causando errori
        quando il cursor è già chiuso.
        """
        try:
            import oracledb
            oracledb.defaults.fetch_lobs = False
        except ImportError:
            pass

    def _crea_engine_mssql(self, conn_str: str, connect_args: dict) -> Engine:
        """
        Crea engine MSSQL con fallback automatico del driver ODBC.

        Tenta prima con ODBC Driver 18 (più recente), e se fallisce
        per mancanza del driver, ritenta con ODBC Driver 17.

        Bulk performance: abilita `fast_executemany=True` per pyodbc.
        Questo trasforma l'executemany standard (1 INSERT per riga, ~100
        round-trip per 1000 righe) in una chiamata `SQLBulkOperations`
        ODBC con array binding nativo. Speedup tipico **10-50x** su
        INSERT batch sopra le ~500 righe.

        Edge case noti di `fast_executemany`:
          - DECIMAL con precisione molto alta (>15) puo' perdere
            precisione: pyodbc usa `SQL_NUMERIC` che ha bound a 15
            cifre significative. CARICA usa `Numeric(precision, scale)`
            entro questa soglia, quindi safe.
          - DATETIME2(7) con precisione 100ns: pyodbc tronca a microseconds
            (6 cifre). Tipi standard `DATETIME`/`DATETIMEOFFSET` ok.

        Disable rapido: env `MSSQL_FAST_EXECUTEMANY=off` per fallback
        all'executemany standard senza redeploy.

        Args:
            conn_str: Connection string con Driver 18.
            connect_args: Argomenti di connessione.

        Returns:
            Engine SQLAlchemy funzionante.
        """
        import os as _os
        fast_em = (_os.getenv("MSSQL_FAST_EXECUTEMANY") or "on").lower()
        engine_kwargs = {}
        if fast_em not in ("off", "false", "0", "no", "disabled"):
            engine_kwargs["fast_executemany"] = True

        try:
            engine = create_engine(
                conn_str, connect_args=connect_args, **engine_kwargs,
            )
            # Verifica che il driver funzioni con una query di test
            with engine.connect() as test_conn:
                test_conn.execute(text("SELECT 1"))
            return engine
        except Exception as e:
            if "ODBC Driver 18" in str(e):
                conn_str_17 = conn_str.replace("ODBC+Driver+18", "ODBC+Driver+17")
                return create_engine(
                    conn_str_17, connect_args=connect_args, **engine_kwargs,
                )
            raise

    def _costruisci_connection_string(self, conn: DatabaseConnection) -> str:
        """
        Costruisce la connection string SQLAlchemy specifica per il tipo database.

        La password viene URL-encoded per gestire caratteri speciali.

        Args:
            conn: Configurazione connessione.

        Returns:
            Connection string SQLAlchemy completa.
        """
        # SECURITY: URL-encode username E password. Prima solo la password era
        # encodata. Se username contiene '@', ':', '/' (es. email-based auth),
        # la URL SQLAlchemy risulta malformata:
        #   postgresql://user@domain:pass@host → il primo @ chiude la sezione auth.
        usr = quote_plus(conn.username)
        pwd = quote_plus(conn.password)

        if conn.type in ("postgres", "postgresql"):
            return (
                f"postgresql+psycopg2://{usr}:{pwd}"
                f"@{conn.host}:{conn.port}/{conn.database}"
            )
        elif conn.type == "mysql":
            return (
                f"mysql+pymysql://{usr}:{pwd}"
                f"@{conn.host}:{conn.port}/{conn.database}"
            )
        elif conn.type == "mssql":
            # Encrypt=Optional: ODBC Driver 18 di default richiede TLS valido,
            # con Optional accetta sia server con TLS che senza, e con
            # TrustServerCertificate=yes accetta self-signed.
            return (
                f"mssql+pyodbc://{usr}:{pwd}"
                f"@{conn.host}:{conn.port}/{conn.database}"
                f"?driver=ODBC+Driver+18+for+SQL+Server"
                f"&TrustServerCertificate=yes&Encrypt=Optional"
            )
        elif conn.type == "oracle":
            return (
                f"oracle+oracledb://{usr}:{pwd}"
                f"@{conn.host}:{conn.port}/?service_name={conn.database}"
            )
        elif conn.type == "sqlite":
            return f"sqlite:///{conn.database}"

        raise ValueError(f"Tipo database '{conn.type}' non supportato")

    def _ottieni_connect_args(self, conn: DatabaseConnection) -> dict:
        """
        Restituisce gli argomenti di connessione specifici per tipo database.

        Include timeout di connessione e lettura/scrittura dove supportati.
        """
        args_per_tipo = {
            # gssencmode=disable evita il tentativo GSSAPI/Kerberos su Windows
            # sslmode=prefer mantiene SSL se disponibile ma non fallisce senza
            "postgres":   {"connect_timeout": 60, "gssencmode": "disable"},  # FIX-GSSAPI
            "postgresql": {"connect_timeout": 60, "gssencmode": "disable"},
            "mysql":      {"connect_timeout": 60, "read_timeout": 60, "write_timeout": 60},
            "mssql":      {"timeout": 60},
            "sqlite":     {"timeout": 60},
            # oracle: oracledb non supporta connect_timeout come kwarg di connessione
        }
        return args_per_tipo.get(conn.type, {})


# =============================================================================
# BUILDER PER RISULTATI (funzioni pure)
# =============================================================================

class ResultBuilder:
    """Costruttore di QueryResult — funzioni pure senza stato."""

    @staticmethod
    def success(
        rows_count: int,
        columns: List[str],
        output_path: str,
        project_id: str,
        timings: Dict[str, float],
        result_id: Optional[str] = None,
    ) -> QueryResult:
        """Costruisce un risultato di successo con tutti i dettagli.

        `result_id` e' il riferimento canonico al blob persistito da
        `save_result_blob`. I caller downstream (task result dict,
        `PipelineArtifact.artifact_kind`) lo usano per il download.
        """
        return QueryResult(
            success=True,
            message=f"Query eseguita. File: {Path(output_path).name}",
            rows_count=rows_count,
            columns=columns,
            output_path=output_path,
            project_id=project_id,
            timings=timings,
            result_id=result_id,
        )

    @staticmethod
    def error(message: str) -> QueryResult:
        """Costruisce un risultato di errore con il messaggio."""
        return QueryResult(
            success=False,
            message=f"Errore esecuzione: {message}"
        )

    @staticmethod
    def to_dict(result: QueryResult) -> Dict[str, Any]:
        """
        Converte un QueryResult in dizionario per la risposta API.

        I campi di dettaglio (righe, colonne, path, timing, result_id)
        vengono inclusi solo in caso di successo.
        """
        d = {
            "success": result.success,
            "message": result.message,
        }
        if result.success:
            d.update({
                "rows_count": result.rows_count,
                "columns": result.columns,
                "output_path": result.output_path,
                "project_id": result.project_id,
                "timings": result.timings,
                "result_id": result.result_id,
            })
        return d


class DataFrameBuilder:
    """Costruttore di DataFrame — funzione pura senza stato."""

    @staticmethod
    def from_rows(rows: List[Tuple], columns: List[str]) -> pd.DataFrame:
        """Crea un DataFrame pandas da una lista di tuple e nomi colonne."""
        return pd.DataFrame(rows, columns=columns)


# =============================================================================
# QUERY EXECUTOR
# =============================================================================

class QueryExecutor:
    """
    Esecutore di query SQL con supporto multi-database.

    Usa SQLAlchemy come layer unificato. Tutte le dipendenze sono iniettabili
    per facilitare il testing (engine factory, funzione salvataggio, logger).

    Metodi principali:
        - execute_raw(): esegue query e restituisce risultati grezzi (righe, colonne)
        - execute_query(): esegue query, salva su file, restituisce metadata

    Dependency Injection:
        - engine_factory: creazione engine SQLAlchemy (mockabile per test)
        - save_function: salvataggio DataFrame su file (mockabile per test)
        - logger: logging (NullLogger per test silenziosi)
        - results_dir: directory per i file di output
    """

    def __init__(
        self,
        engine_factory: EngineFactoryProtocol = None,
        save_function: SaveFunctionProtocol = None,
        logger: LoggerProtocol = None,
        results_dir: Path = None
    ):
        """
        Inizializza l'executor con le dipendenze iniettate.

        Tutti i parametri sono opzionali: se non forniti, vengono usati
        i default (DefaultEngineFactory, NullLogger, salvataggio CSV).

        Args:
            engine_factory: Factory per SQLAlchemy engines.
            save_function: Funzione per salvare DataFrame su file.
            logger: Logger per messaggi di info/error/debug.
            results_dir: Directory base per i file di output.
        """
        self._engine_factory = engine_factory or DefaultEngineFactory()
        self._save = save_function or self._salvataggio_default
        self._logger = logger or NullLogger()
        self._results_dir = results_dir or Path("/tmp/results")

        # Componenti interni (funzioni pure senza stato)
        self._result_builder = ResultBuilder
        self._df_builder = DataFrameBuilder

    @staticmethod
    def _salvataggio_default(
        df: pd.DataFrame,
        output_path: Path,
        format: str,
        geom_column: Optional[str] = None
    ) -> Path:
        """
        Salvataggio di fallback — supporta solo formati base (CSV, JSON, XLSX).

        In produzione viene sostituito da app.utils.dataframe.save_dataframe
        che supporta anche i formati geospaziali.
        """
        final_path = output_path.with_suffix(f".{format}")
        if format == "csv":
            df.to_csv(final_path, index=False, encoding="utf-8")
        elif format == "json":
            df.to_json(final_path, orient="records", indent=2)
        elif format == "xlsx":
            df.to_excel(final_path, index=False)
        else:
            # Fallback: salva come CSV
            final_path = output_path.with_suffix(".csv")
            df.to_csv(final_path, index=False, encoding="utf-8")
        return final_path

    @property
    def RESULTS_DIR(self) -> Path:
        """Directory risultati (per compatibilità con codice esistente)."""
        return self._results_dir

    # =========================================================================
    # ESECUZIONE RAW (metodo unificato)
    # =========================================================================

    def execute_raw(
        self,
        connection: DatabaseConnection,
        sql: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[tuple], List[str]]:
        """
        Esegue una query SQL e restituisce i risultati grezzi.

        Metodo unificato usato sia da execute_query (con salvataggio)
        che da preview (senza salvataggio). Gestisce le specificità
        di ogni database.

        Gestione Oracle:
            - ALTER SESSION SET OPEN_CURSORS = 32767 per le funzioni SDO
              che aprono cursori PL/SQL interni per ogni riga
            - Normalizzazione nomi colonne in lowercase (Oracle li restituisce
              in UPPERCASE per default)

        Args:
            connection: Configurazione della connessione database.
            sql: Query SQL con placeholder :param_name per parametri preparati.
            params: Dizionario dei parametri (es. {"limit": 100}).

        Returns:
            Tupla (righe, colonne) dove:
                - righe: lista di tuple con i valori
                - colonne: lista di nomi delle colonne
        """
        engine = self._engine_factory.create_engine(connection)

        with engine.connect() as conn:
            # Propaga DB_QUERY_TIMEOUT come statement_timeout per ogni DB che lo supporta
            try:
                from app.config import settings
                timeout_s  = settings.DB_QUERY_TIMEOUT          # secondi (es. 30)
                # SECURITY: cast a int per prevenire injection se config contaminata
                timeout_ms = int(timeout_s) * 1000

                if connection.type in ("postgres", "postgresql"):
                    # PostgreSQL: statement_timeout in ms — parametrizzato
                    conn.execute(text("SET statement_timeout = :t"), {"t": timeout_ms})

                elif connection.type == "mysql":
                    # MySQL 5.7.8+: max_execution_time in ms (solo SELECT) — parametrizzato
                    conn.execute(text("SET SESSION max_execution_time = :t"), {"t": timeout_ms})

                elif connection.type == "oracle":
                    # Oracle: callTimeout in ms sull'oggetto connessione nativo
                    raw = conn.connection.dbapi_connection
                    if hasattr(raw, "callTimeout"):
                        raw.callTimeout = timeout_ms

            except Exception as e:
                # Non bloccante — il timeout di default di Celery fa da fallback.
                logger.debug(
                    "set timeout di sessione fallito (db=%s), uso fallback Celery: %s: %s",
                    connection.type,
                    type(e).__name__,
                    e,
                )

            # Oracle: aumenta limite cursori aperti per funzioni SDO_UTIL/SDO_GEOM
            if connection.type == "oracle":
                try:
                    conn.execute(text("ALTER SESSION SET OPEN_CURSORS = 32767"))
                except Exception as e:
                    # Prosegui con il default se non ha i permessi.
                    logger.debug(
                        "ALTER SESSION OPEN_CURSORS fallito (probabili permessi), uso default: %s: %s",
                        type(e).__name__,
                        e,
                    )

            # Esecuzione query con parametri preparati (sicurezza SQL injection)
            result = conn.execute(text(sql), params or {})

            # Estrazione nomi colonne dal cursor nativo del database
            columns = [desc[0] for desc in result.cursor.description]

            # Oracle: normalizza nomi colonne da UPPERCASE a lowercase
            if connection.type == "oracle":
                columns = [c.lower() for c in columns]

            # Materializza righe in blocchi.
            # C11: hard cap (ESTRAI_MAX_ROWS) per evitare OOM. Pre-fix il
            # chunking era cosmetico (fetchmany ma accumulo totale in
            # memoria), quindi una query mal filtrata da 100M righe
            # crashava il worker. Ora se il cumulativo supera il cap la
            # query fallisce loud con suggerimento operativo.
            from app.config import settings as _s
            _max_rows = int(getattr(_s, "ESTRAI_MAX_ROWS", 5_000_000) or 0)
            CHUNK_SIZE = 10_000
            rows = []
            while True:
                chunk = result.fetchmany(CHUNK_SIZE)
                if not chunk:
                    break
                rows.extend(tuple(row) for row in chunk)
                if _max_rows > 0 and len(rows) > _max_rows:
                    raise ValueError(
                        f"Query ha ritornato piu' di {_max_rows:,} righe "
                        f"(letti {len(rows):,}+ in streaming). "
                        f"Aggiungi un filtro WHERE o un LIMIT alla query, "
                        f"oppure alza ESTRAI_MAX_ROWS se la dimensione e' "
                        f"attesa. Cap in vigore per prevenire OOM del worker."
                    )

            return rows, columns

    # =========================================================================
    # C11: STREAMING CSV / PARQUET (zero full-materialization)
    # =========================================================================

    def _execute_streaming_to_file(
        self,
        connection: DatabaseConnection,
        sql: str,
        params: Optional[Dict[str, Any]],
        output_path: "Path",
        output_format: str,
    ) -> Tuple[int, List[str]]:
        """Esegue la query e scrive l'output su file in streaming
        senza materializzare l'intero result set in memoria.

        Supporta CSV e Parquet. Per CSV scrive riga-per-riga via
        csv.writer. Per Parquet usa pyarrow.parquet.ParquetWriter
        con scrittura per RecordBatch (chunk da 10k).

        Return: (righe_totali, nomi_colonne).

        C11: prima del fix il chunking era cosmetico (fetchmany ma
        accumulo in list + DataFrame intero). Ora il peak memory e'
        proporzionale al chunk (10k righe), non al result set.
        """
        import csv as _csv
        from pathlib import Path as _Path
        output_path = _Path(output_path)

        engine = self._engine_factory.create_engine(connection)
        from app.config import settings as _s
        _max_rows = int(getattr(_s, "ESTRAI_MAX_ROWS", 5_000_000) or 0)
        CHUNK_SIZE = 10_000
        total_rows = 0

        with engine.connect() as conn:
            # Stesso setup timeout di execute_raw (duplicato qui per
            # mantenere boundary chiari; factor-out sarebbe refactor fuori
            # scope C11).
            try:
                timeout_s = _s.DB_QUERY_TIMEOUT
                timeout_ms = int(timeout_s) * 1000
                if connection.type in ("postgres", "postgresql"):
                    conn.execute(text("SET statement_timeout = :t"), {"t": timeout_ms})
                elif connection.type == "mysql":
                    conn.execute(text("SET SESSION max_execution_time = :t"), {"t": timeout_ms})
                elif connection.type == "oracle":
                    raw = conn.connection.dbapi_connection
                    if hasattr(raw, "callTimeout"):
                        raw.callTimeout = timeout_ms
            except Exception as e:
                # Non bloccante — il timeout di default di Celery fa da fallback.
                logger.debug(
                    "set timeout di sessione fallito (db=%s), uso fallback Celery: %s: %s",
                    connection.type,
                    type(e).__name__,
                    e,
                )

            if connection.type == "oracle":
                try:
                    conn.execute(text("ALTER SESSION SET OPEN_CURSORS = 32767"))
                except Exception as e:
                    # Prosegui con il default se non ha i permessi.
                    logger.debug(
                        "ALTER SESSION OPEN_CURSORS fallito (probabili permessi), uso default: %s: %s",
                        type(e).__name__,
                        e,
                    )

            result = conn.execute(text(sql), params or {})
            columns = [desc[0] for desc in result.cursor.description]
            if connection.type == "oracle":
                columns = [c.lower() for c in columns]

            if output_format == "csv":
                with open(output_path, "w", newline="", encoding="utf-8") as fp:
                    writer = _csv.writer(fp)
                    writer.writerow(columns)
                    while True:
                        chunk = result.fetchmany(CHUNK_SIZE)
                        if not chunk:
                            break
                        writer.writerows(chunk)
                        total_rows += len(chunk)
                        if _max_rows > 0 and total_rows > _max_rows:
                            raise ValueError(
                                f"Query ha ritornato piu' di {_max_rows:,} "
                                f"righe (streaming). Aggiungi WHERE/LIMIT "
                                f"o alza ESTRAI_MAX_ROWS."
                            )

            elif output_format == "parquet":
                import pyarrow as _pa
                import pyarrow.parquet as _pq
                pq_writer = None
                try:
                    while True:
                        chunk = result.fetchmany(CHUNK_SIZE)
                        if not chunk:
                            break
                        df_chunk = pd.DataFrame(chunk, columns=columns)
                        table = _pa.Table.from_pandas(df_chunk, preserve_index=False)
                        if pq_writer is None:
                            pq_writer = _pq.ParquetWriter(output_path, table.schema)
                        pq_writer.write_table(table)
                        total_rows += len(chunk)
                        if _max_rows > 0 and total_rows > _max_rows:
                            raise ValueError(
                                f"Query ha ritornato piu' di {_max_rows:,} "
                                f"righe (streaming). Aggiungi WHERE/LIMIT "
                                f"o alza ESTRAI_MAX_ROWS."
                            )
                finally:
                    if pq_writer is not None:
                        pq_writer.close()
            else:
                raise ValueError(
                    f"_execute_streaming_to_file: formato non supportato "
                    f"per streaming: {output_format}. Use csv o parquet."
                )

        return total_rows, columns

    # =========================================================================
    # ESECUZIONE COMPLETA (con salvataggio file)
    # =========================================================================

    def execute_query(
        self,
        connection: DatabaseConnection,
        sql: str,
        project_id: str,
        output_filename: str,
        output_format: str = "csv",
        params: Optional[Dict[str, Any]] = None,
        geom_column: Optional[str] = None,
        group_id: Optional[str] = None,
        run_id: Optional[str] = None,
        node_id: Optional[str] = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Esegue una query SQL, salva i risultati su file e restituisce i metadata.

        Pipeline completa:
            1. Esecuzione query (via execute_raw)
            2. Conversione risultati in DataFrame
            3. Salvataggio nel formato richiesto
            4. Restituzione metadata (righe, colonne, path, timing)

        Args:
            connection: Configurazione della connessione database.
            sql: Query SQL con placeholder :param_name.
            project_id: ID del progetto (per organizzazione directory output).
            output_filename: Nome del file di output (senza estensione).
            output_format: Formato di output (csv, json, xlsx, geojson, gpkg, shp).
            params: Dizionario dei parametri per la query preparata.
            geom_column: Nome colonna geometria per export geospaziale.
            intermediate_mode_override: R-25 PR-B2. Override per-call di
                `INTERMEDIATE_PERSISTENCE_MODE` propagato a
                `record_final_snapshot(mode_override=...)`. None =
                bit-compat (legge il default globale dalle settings).
                Override invalido propaga `IntermediateSettingsError`
                fail-loud al chiamante. Kwarg esplicito per evitare
                stato d'istanza fragile su QueryExecutor (un singolo
                istanza puo' essere riusata da chiamanti con override
                diversi nello stesso processo).

        Returns:
            Dizionario con: success, message, rows_count, columns, output_path, timings.
        """
        timings = {}
        inizio_totale = time.time()

        # C11: path streaming per CSV/Parquet (zero full-materialization).
        # Attivato solo se non c'e' geometria (export geospaziale richiede
        # trasformazioni WKT/WKB sulla colonna geom che non si prestano al
        # chunk-write minimal). Per gli altri formati (xlsx, geojson, gpkg,
        # shp, json): path legacy in-memory coperto dal cap
        # ESTRAI_MAX_ROWS.
        _streamable = output_format in ("csv", "parquet") and not geom_column

        try:
            self._logger.info(f"Esecuzione query per progetto {project_id}")

            if _streamable:
                # STREAMING PATH (CSV / Parquet senza geometria)
                t_query = time.time()
                output_path = get_temp_output_path(output_filename)
                output_path = output_path.with_suffix(f".{output_format}")
                row_count, columns = self._execute_streaming_to_file(
                    connection=connection,
                    sql=sql,
                    params=params,
                    output_path=output_path,
                    output_format=output_format,
                )
                saved_path = output_path
                # df resta None: in streaming non costruiamo DataFrame
                # (risparmia un'intera pass di memoria pari al result set).
                df = None
                timings["query_total"] = time.time() - t_query
                timings["dataframe"] = 0.0
                timings["save"] = 0.0  # fuso dentro query_total
                self._logger.info(
                    f"Streaming {output_format} completato: "
                    f"{row_count} righe in {timings['query_total']:.2f}s "
                    f"(peak memory ~= 1 chunk, non il result set intero)"
                )
            else:
                # LEGACY PATH (fetch → DataFrame → save, capped da ESTRAI_MAX_ROWS)
                t_query = time.time()
                rows, columns = self.execute_raw(connection, sql, params)
                timings["query_total"] = time.time() - t_query
                self._logger.info(f"Query totale: {timings['query_total']:.2f}s")

                t_df = time.time()
                df = self._df_builder.from_rows(rows, columns)
                timings["dataframe"] = time.time() - t_df
                self._logger.info(
                    f"Creazione DataFrame ({len(rows)} righe): "
                    f"{timings['dataframe']:.2f}s"
                )

                row_count = len(rows)

            # Salva risultato.
            # - Streaming path: il file esiste gia' su disco, usiamo
            #   save_result_blob (path-based) che lo legge e lo cancella.
            # - Legacy path: delegamo a save_pipeline_result (single
            #   entry-point che fa in-memory per formati serializzabili
            #   e fallback FS trasparente per gpkg/shp).
            t_save = time.time()
            if _streamable:
                result_id = save_result_blob(
                    project_id, saved_path, output_format, row_count,
                    group_id=group_id,
                )
            else:
                from app.utils.result_store import save_pipeline_result
                saved = save_pipeline_result(
                    project_id=project_id,
                    df=df,
                    output_format=output_format,
                    filename_stem=output_filename,
                    rows_count=row_count,
                    group_id=group_id,
                    geom_column=geom_column,
                )
                result_id = saved["result_id"]
                saved_path = Path(saved["output_filename"])
            timings["save"] = time.time() - t_save
            self._logger.info(f"Salvataggio file ({output_format}): {timings['save']:.2f}s")

            timings["total"] = time.time() - inizio_totale
            self._logger.info(f"TOTALE: {timings['total']:.2f}s")

            # Snapshot intermediate (non bloccante).
            # Phase 6 Batch 3A (2026-04-28): passa per `record_final_snapshot`
            # con module="estrai". La policy globale
            # `INTERMEDIATE_PERSISTENCE_MODE` decide il comportamento:
            #   - metadata_only -> solo node_meta + node_hash, zero righe
            #   - preview_only  -> meta + hash + prime N righe (default 500)
            #   - full_pg       -> tutte le righe (legacy)
            # Niente cap custom legacy (`INTERMEDIATE_SNAPSHOT_MAX_ROWS`):
            # la policy centrale gestisce il dimensionamento.
            #
            # Streaming (df=None): conserviamo lo status "ok_streamed" via
            # write_node legacy perche' qui non c'e' un DataFrame da
            # fingerprintare; il record_final_snapshot policy-based richiede
            # un DataFrame. Lo streaming era gia' un branch dedicato e
            # rimane tale fino all'introduzione di un fingerprint streaming
            # (out of scope Batch 3).
            if run_id and node_id:
                try:
                    if df is None:
                        # Streaming branch: niente DataFrame -> meta-only legacy.
                        from app.core.intermediate.writer import write_node
                        write_node(
                            run_id=run_id,
                            node_id=node_id,
                            node_type="estrai",
                            rows=[],
                            col_schema={c: "unknown" for c in columns},
                            exec_time=timings.get("total", 0),
                            status="ok_streamed",
                        )
                    else:
                        from app.core.intermediate.snapshot import record_final_snapshot
                        record_final_snapshot(
                            run_id=run_id,
                            module="estrai",
                            node_id=node_id,
                            node_type="estrai",
                            df=df,
                            exec_time=timings.get("total", 0),
                            status="ok",
                            mode_override=intermediate_mode_override,
                        )
                    self._logger.info(f"Intermediate snapshot: run={run_id} node={node_id}")
                except Exception as _iex:
                    self._logger.warning(f"Intermediate write fallita (non bloccante): {_iex}")

            result = self._result_builder.success(
                rows_count=row_count,
                columns=columns,
                output_path=str(saved_path),
                project_id=project_id,
                timings=timings,
                result_id=result_id,
            )
            return self._result_builder.to_dict(result)

        except Exception as e:
            self._logger.error(f"[GESTITO] Errore esecuzione query: {str(e)}")
            result = self._result_builder.error(str(e))
            return self._result_builder.to_dict(result)


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_executor(
    engine_factory: EngineFactoryProtocol = None,
    save_function: SaveFunctionProtocol = None,
    results_dir: Path = None,
    logger: LoggerProtocol = None
) -> QueryExecutor:
    """
    Factory function per creare un QueryExecutor con Dependency Injection.

    Tutti i parametri sono opzionali — utile per configurazione
    flessibile e testing con mock.

    Args:
        engine_factory: Factory per SQLAlchemy engines.
        save_function: Funzione per salvare DataFrame.
        results_dir: Directory base per i file di output.
        logger: Logger per i messaggi.

    Returns:
        Istanza di QueryExecutor configurata.
    """
    return QueryExecutor(
        engine_factory=engine_factory,
        save_function=save_function,
        results_dir=results_dir,
        logger=logger
    )
