"""
Esecutore di query SQL unificato per PLENORA-ETL.

Gestisce l'esecuzione di query su tutti i database supportati tramite
"""

import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Protocol
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.query_engines import DefaultEngineFactory
from app.db.query_session import ExecutionContext, apply_session_context
from app.db.query_streaming import execute_streaming_to_file
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Result store — salva blob nel DB e usa temp files
from app.utils.result_store import get_temp_output_path, save_result_blob


# DEFINIZIONI TIPI

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


# PROTOCOLLI PER DEPENDENCY INJECTION

class EngineFactoryProtocol(Protocol):
    """Protocollo per la factory di SQLAlchemy engines — iniettabile per test."""
    def create_engine(self, conn: DatabaseConnection) -> Engine: ...


class LoggerProtocol(Protocol):
    """Protocollo per il logger — iniettabile per test silenziosi."""
    def info(self, msg: str, *args: Any) -> None: ...
    def error(self, msg: str, *args: Any) -> None: ...
    def debug(self, msg: str, *args: Any) -> None: ...
    def warning(self, msg: str, *args: Any) -> None: ...


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
    def info(self, msg: str, *args: Any) -> None: pass
    def error(self, msg: str, *args: Any) -> None: pass
    def debug(self, msg: str, *args: Any) -> None: pass
    def warning(self, msg: str, *args: Any) -> None: pass



# BUILDER PER RISULTATI (funzioni pure

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


# QUERY EXECUTOR

class QueryExecutor:
    """
    Esecutore di query SQL con supporto multi-database.

    Usa SQLAlchemy come layer unificato. Tutte le dipendenze sono iniettabili
    per facilitare il testing (engine factory, funzione salvataggio, logger).

    Metodi principali:
        - execute_raw: esegue query e restituisce risultati grezzi (righe, colonne)
        - execute_query: esegue query, salva su file, restituisce metadata

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

        # Componenti interni (funzioni pure senza stato
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

    def _build_execution_context(
        self,
        connection: DatabaseConnection,
        sql: str,
        params: Optional[Dict[str, Any]],
    ) -> ExecutionContext:
        """Build typed execution/session metadata for one query."""
        try:
            from app.config import settings
            timeout_seconds = int(settings.DB_QUERY_TIMEOUT)
        except Exception as e:
            timeout_seconds = 0
            logger.debug(
                "lettura DB_QUERY_TIMEOUT fallita, timeout sessione disabilitato: %s: %s",
                type(e).__name__,
                e,
            )
        return ExecutionContext.for_query(
            connection=connection,
            sql=sql,
            params=params,
            timeout_seconds=timeout_seconds,
        )

    # ESECUZIONE RAW (metodo unificato

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
        execution = self._build_execution_context(connection, sql, params)
        engine = self._engine_factory.create_engine(execution.connection)

        with engine.connect() as conn:
            apply_session_context(conn, execution.session, logger)

            # Esecuzione query con parametri preparati (sicurezza SQL injection
            result = conn.execute(text(execution.sql), dict(execution.params))

            # Estrazione nomi colonne dal cursor nativo del database
            columns = [desc[0] for desc in result.cursor.description]

            # Oracle: normalizza nomi colonne da UPPERCASE a lowercase
            if execution.session.provider == "oracle":
                columns = [c.lower() for c in columns]

            # Materializza righe in blocchi
            # C11: hard cap (ESTRAI_MAX_ROWS) per evitare OOM. Pre-fix il
            # chunking era cosmetico (fetchmany ma accumulo totale in
            # memoria), quindi una query mal filtrata da 100M righe
            # crashava il worker. Ora se il cumulativo supera il cap la
            # query fallisce loud con suggerimento operativo
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

    # C11: STREAMING CSV / PARQUET (zero full-materialization

    def _execute_streaming_to_file(
        self,
        connection: DatabaseConnection,
        sql: str,
        params: Optional[Dict[str, Any]],
        output_path: "Path",
        output_format: str,
    ) -> Tuple[int, List[str]]:
        """Compatibility delegate for CSV/Parquet streaming extraction."""
        return execute_streaming_to_file(
            engine_factory=self._engine_factory,
            connection=connection,
            sql=sql,
            params=params,
            output_path=Path(output_path),
            output_format=output_format,
        )

    # ESECUZIONE COMPLETA (con salvataggio file

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

        # C11: path streaming per CSV/Parquet (zero full-materialization
        # Attivato solo se non c'e' geometria (export geospaziale richiede
        # trasformazioni WKT/WKB sulla colonna geom che non si prestano al
        # chunk-write minimal). Per gli altri formati (xlsx, geojson, gpkg
        # shp, json): path legacy in-memory coperto dal cap
        # ESTRAI_MAX_ROWS
        _streamable = output_format in ("csv", "parquet") and not geom_column

        try:
            self._logger.info(f"Esecuzione query per progetto {project_id}")

            if _streamable:
                # STREAMING PATH (CSV / Parquet senza geometria
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
                # risparmia un'intera pass di memoria pari al result set
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
                # NOTE: fallback fetch -> DataFrame -> save, capped da ESTRAI_MAX_ROWS.
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

            # Salva risultato
            # Streaming path: il file esiste gia' su disco, usiamo
            # save_result_blob (path-based) che lo legge e lo cancella
            # Fallback path: delegamo a save_pipeline_result (single
            # entry-point che fa in-memory per formati serializzabili
            # e fallback FS trasparente per gpkg/shp
            t_save = time.time()
            if _streamable:
                result_id = save_result_blob(
                    project_id, saved_path, output_format, row_count,
                    group_id=group_id,
                )
            else:
                from app.utils.result_store import save_pipeline_result_outcome
                saved = save_pipeline_result_outcome(
                    project_id=project_id,
                    df=df,
                    output_format=output_format,
                    filename_stem=output_filename,
                    rows_count=row_count,
                    group_id=group_id,
                    geom_column=geom_column,
                )
                result_id = saved.result_id
                saved_path = Path(saved.output_filename)
            timings["save"] = time.time() - t_save
            self._logger.info(f"Salvataggio file ({output_format}): {timings['save']:.2f}s")

            timings["total"] = time.time() - inizio_totale
            self._logger.info(f"TOTALE: {timings['total']:.2f}s")

            # Snapshot intermediate (non bloccante
            # passa per `record_final_snapshot`
            # con module="estrai". La policy globale
            # `INTERMEDIATE_PERSISTENCE_MODE` decide il comportamento
            # metadata_only -> solo node_meta + node_hash, zero righe
            # preview_only -> meta + hash + prime N righe (default 500
            # full_pg -> tutte le righe (legacy
            # Niente cap custom legacy (`INTERMEDIATE_SNAPSHOT_MAX_ROWS`
            # la policy centrale gestisce il dimensionamento
            # Streaming (df=None): conserviamo lo status "ok_streamed" via
            # write_node legacy perche' qui non c'e' un DataFrame da
            # fingerprintare; il record_final_snapshot policy-based richiede
            # un DataFrame. Lo streaming era gia' un branch dedicato e
            # rimane tale fino all'introduzione di un fingerprint streaming
            # out of scope
            if run_id and node_id:
                try:
                    if df is None:
                        # Streaming branch: niente DataFrame -> meta-only legacy
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


# FACTORY FUNCTION

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
