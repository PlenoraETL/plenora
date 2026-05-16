"""
SQLAlchemy engine factory helpers for query execution.

This module stays independent from QueryExecutor so engine construction can be
tested without importing the executor orchestration layer.
"""

from typing import Protocol
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db.query_session import QueryConnection
from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger


logger = get_logger(__name__)


class EngineConnection(QueryConnection, Protocol):
    """Connection shape required by DefaultEngineFactory."""


class DefaultEngineFactory:
    """
    Factory di default per creare SQLAlchemy engines con connection pooling.

    Costruisce la connection string appropriata per ogni tipo di database
    e gestisce configurazioni specifiche:
        - Oracle: disabilita fetch LOB automaticamente (CLOB/BLOB -> str/bytes)
        - MSSQL: fallback automatico da ODBC Driver 18 a Driver 17
        - Tutti: timeout di connessione configurati per tipo

    Performance:
        Mantiene un pool di engine cached per connection key (type:host:port:db:user).
        Evita di creare e distruggere un engine per ogni query.
    """

    SUPPORTED_TYPES = ("postgres", "postgresql", "mysql", "mssql", "oracle", "sqlite")

    _MAX_ENGINES = 50

    def __init__(self):
        self._engine_cache = {}

    def _connection_key(self, conn: EngineConnection) -> str:
        """Chiave univoca per identificare una connessione nel pool."""
        return f"{conn.type}:{conn.host}:{conn.port}:{conn.database}:{conn.username}"

    def create_engine(self, conn: EngineConnection) -> Engine:
        """
        Restituisce un engine pooled per la connessione specificata.

        Se esiste gia' un engine per questa connessione, lo riutilizza.
        Altrimenti ne crea uno nuovo con pool_pre_ping=True per verificare
        che le connessioni nel pool siano ancora valide.

        Args:
            conn: Configurazione della connessione database.

        Returns:
            Engine SQLAlchemy pronto per l'uso.

        Raises:
            ValueError: Se il tipo di database non e' supportato.
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
        quando il cursor e' gia' chiuso.
        """
        try:
            import oracledb
            oracledb.defaults.fetch_lobs = False
        except ImportError:
            pass

    def _crea_engine_mssql(self, conn_str: str, connect_args: JsonObject) -> Engine:
        """
        Crea engine MSSQL con fallback automatico del driver ODBC.

        Tenta prima con ODBC Driver 18 (piu' recente), e se fallisce
        per mancanza del driver, ritenta con ODBC Driver 17.

        Bulk performance: abilita `fast_executemany=True` per pyodbc.
        Questo trasforma l'executemany standard (1 INSERT per riga, ~100
        round-trip per 1000 righe) in una chiamata `SQLBulkOperations`
        ODBC con array binding nativo. Speedup tipico **10-50x** su
        INSERT le ~500 righe.

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
        # BE-CONFIG-001D: default tipato in `Settings.DEFAULTS["MSSQL_FAST_EXECUTEMANY"]="on"`
        # Override env continua a funzionare via Settings
        from app.config import settings as _settings
        fast_em = (_settings.get("MSSQL_FAST_EXECUTEMANY") or "on").lower()
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

    def _costruisci_connection_string(self, conn: EngineConnection) -> str:
        """
        Costruisce la connection string SQLAlchemy specifica per il tipo database.

        La password viene URL-encoded per gestire caratteri speciali.

        Args:
            conn: Configurazione connessione.

        Returns:
            Connection string SQLAlchemy completa.
        """
        # SECURITY: URL-encode username E password. Prima solo la password era
        # encodata. Se username contiene '@', ':', '/' (es. email-based auth)
        # la URL SQLAlchemy risulta malformata
        # postgresql://user@domain:pass@host -> il primo @ chiude la sezione auth
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
            # Encrypt=Optional: ODBC Driver 18 di default richiede TLS valido
            # con Optional accetta sia server con TLS che senza, e con
            # TrustServerCertificate=yes accetta self-signed
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

    def _ottieni_connect_args(self, conn: EngineConnection) -> JsonObject:
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
