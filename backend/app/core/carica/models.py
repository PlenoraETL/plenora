"""
Modelli dati per il modulo CARICA.

Keeps database loading behavior isolated from orchestration callers.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


class WriteMode(str, Enum):
    """Modalità di scrittura su database.

    CREATE: crea nuova tabella (errore se esiste già)
    APPEND: inserisce righe in tabella esistente
    REPLACE: elimina tabella esistente e la ricrea
    UPDATE: aggiorna righe esistenti tramite match su colonne chiave
    UPSERT: inserisce nuove righe, aggiorna quelle esistenti (match su PK)
    TRUNCATE_INSERT: svuota tabella e reinserisce tutte le righe
    """
    CREATE = "create"
    APPEND = "append"
    REPLACE = "replace"
    UPDATE = "update"
    UPSERT = "upsert"
    TRUNCATE_INSERT = "truncate_insert"


class WriteStatus(str, Enum):
    """Stato finale dell'operazione CARICA.

    SUCCESS: tutte le righe scritte con successo
    PARTIAL: alcune righe scritte, alcune fallite (solo con per_batch)
    CONFLICT: conflitti rilevati nel pre-check (APPEND), operazione non eseguita
    VALIDATION_ERROR: errore nella validazione pre-scrittura
    CONNECTION_ERROR: impossibile connettersi al database destinazione
    WRITE_ERROR: errore durante la scrittura (query fallita, timeout, ecc.)
    PERMISSION_ERROR: permessi insufficienti (CREATE TABLE, INSERT, ecc.)
    SKIPPED_IDEMPOTENT: run_id gia' completato in precedenza — no-op sicuro.
        I caller che trattano il risultato come "failure/retry"
        devono includerlo tra i success state, altrimenti innescano
        retry loop.
    """
    SUCCESS = "success"
    PARTIAL = "partial"
    CONFLICT = "conflict"
    VALIDATION_ERROR = "validation_error"
    CONNECTION_ERROR = "connection_error"
    WRITE_ERROR = "write_error"
    PERMISSION_ERROR = "permission_error"
    SKIPPED_IDEMPOTENT = "skipped_idempotent"


class TransactionMode(str, Enum):
    """Strategia transazionale per la scrittura.

    ALL_OR_NOTHING: unica transazione, rollback totale su errore.
        Pro: consistenza garantita.
        Contro: con milioni di righe può essere lento e consumare memoria.

    PER_BATCH: commit ogni N righe (batch_size).
        Pro: più performante, meno memoria.
        Contro: in caso di errore, i à committati rimangono.
    """
    ALL_OR_NOTHING = "all_or_nothing"
    PER_BATCH = "per_batch"


class ConflictPolicy(str, Enum):
    """Policy automatica per conflitti — usata dal CRON quando non c'è utente.

    SKIP: inserisci solo le righe non-conflittuali, salta le altre.
    FORCE: sovrascrivi i record esistenti con i nuovi valori.
    ABORT: interrompi l'operazione e segnala errore al log.
    """
    SKIP = "skip"
    FORCE = "force"
    ABORT = "abort"


@dataclass
class ColumnMapping:
    """Mappatura di una singola colonna sorgente → destinazione.

    Attributes:
        source_column: nome colonna nel DataFrame sorgente
        dest_column: nome colonna nella tabella destinazione
        dest_type: tipo SQL destinazione (es. "VARCHAR(255)", "INTEGER", "GEOMETRY")
        is_geometry: True se è la colonna geometria
        srid: SRID della geometria (propagato dal canvas, es. 4326, 32632)
        is_primary_key: True se è parte della PK (per UPDATE/UPSERT)
        skip: True per escludere la colonna dalla scrittura
    """
    source_column: str
    dest_column: str
    dest_type: str = ""
    is_geometry: bool = False
    srid: Optional[int] = None
    is_primary_key: bool = False
    skip: bool = False


@dataclass
class WriteConfig:
    """Configurazione completa del nodo CARICA.

    Viene serializzata nel canvas JSON come data del nodo "carica".
    Corrisponde al JSON nel campo `data` del nodo canvas.

    Attributes:
        connection_id: ID connessione database (stessa di ESTRAI)
        schema_name: schema DB destinazione (es. "public", "dbo", schema Oracle)
        table_name: nome tabella destinazione (scritto dall'utente)
        write_mode: modalità di scrittura (create, append, update, upsert, ecc.)
        column_mappings: lista mappature colonna sorgente → destinazione
        match_columns: colonne per match in UPDATE/UPSERT (chiavi primarie)
        update_columns: colonne da aggiornare in UPDATE (vuoto = tutte)
        geometry_column: nome colonna geometria destinazione
        geometry_srid: SRID della geometria (dal canvas)
        batch_size: numero righe per batch (default 1000)
        transaction_mode: strategia transazionale (default ALL_OR_NOTHING)
        create_spatial_index: crea indice spaziale su CREATE (default True)
        conflict_policy: policy per CRON automatico (default SKIP)
        if_table_exists: comportamento se tabella esiste con CREATE ("error" o "replace")
        allow_unsafe_append: opt-in esplicito per la combo APPEND + PER_BATCH
            + nessun match_columns. Default False: la combo viene rifiutata
            con ValueError dal writer. Settare True
            solo se la tabella e' append-only con lineage esterno e si
            accetta consapevolmente il rischio di duplicati su retry parziale.
    """
    connection_id: str
    schema_name: str = "public"
    table_name: str = ""
    write_mode: WriteMode = WriteMode.CREATE
    column_mappings: List[ColumnMapping] = field(default_factory=list)
    match_columns: List[str] = field(default_factory=list)
    update_columns: List[str] = field(default_factory=list)
    geometry_column: str = "geom"
    geometry_srid: int = 4326
    batch_size: int = 1000
    transaction_mode: TransactionMode = TransactionMode.ALL_OR_NOTHING
    create_spatial_index: bool = True
    conflict_policy: ConflictPolicy = ConflictPolicy.SKIP
    if_table_exists: str = "error"
    unique_key_column: str = ""
    allow_unsafe_append: bool = False

    @staticmethod
    def from_node_data(data: Dict[str, Any]) -> "WriteConfig":
        """Crea WriteConfig dal campo `data` di un nodo canvas.

        Il frontend invia i dati come dizionario JSON. Questo metodo
        gestisce la conversione dei tipi (stringhe enum, lista mappature).
        """
        mappings = []
        for m in data.get("column_mappings", []):
            if isinstance(m, dict):
                normalized = {
                    "source_column": m.get("source_column") or m.get("source", ""),
                    "dest_column": m.get("dest_column") or m.get("dest", ""),
                    "dest_type": m.get("dest_type", "")
                }
                mappings.append(ColumnMapping(**normalized))
            elif isinstance(m, ColumnMapping):
                mappings.append(m)

        return WriteConfig(
            connection_id=data.get("connection_id", ""),
            schema_name=data.get("schema_name", "public"),
            table_name=data.get("table_name", ""),
            write_mode=WriteMode(data.get("write_mode", "create")),
            column_mappings=mappings,
            match_columns=data.get("match_columns", []),
            update_columns=data.get("update_columns", []),
            geometry_column=data.get("geometry_column", "geom"),
            geometry_srid=int(data.get("geometry_srid", 4326)),
            batch_size=int(data.get("batch_size", 1000)),
            transaction_mode=TransactionMode(
                data.get("transaction_mode", "all_or_nothing")
            ),
            create_spatial_index=data.get("create_spatial_index", True),
            conflict_policy=ConflictPolicy(data.get("conflict_policy", "skip")),
            if_table_exists=data.get("if_table_exists", "error"),
            unique_key_column=data.get("unique_key_column", ""),
            # Strict identity check prevents strings like "false" enabling
            # the unsafe append mode through Python truthiness.
            allow_unsafe_append=data.get("allow_unsafe_append") is True,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializza in dizionario per il canvas JSON."""
        return {
            "connection_id": self.connection_id,
            "schema_name": self.schema_name,
            "table_name": self.table_name,
            "write_mode": self.write_mode.value,
            "column_mappings": [
                {
                    "source_column": m.source_column,
                    "dest_column": m.dest_column,
                    "dest_type": m.dest_type,
                    "is_geometry": m.is_geometry,
                    "srid": m.srid,
                    "is_primary_key": m.is_primary_key,
                    "skip": m.skip,
                }
                for m in self.column_mappings
            ],
            "match_columns": self.match_columns,
            "update_columns": self.update_columns,
            "geometry_column": self.geometry_column,
            "geometry_srid": self.geometry_srid,
            "batch_size": self.batch_size,
            "transaction_mode": self.transaction_mode.value,
            "create_spatial_index": self.create_spatial_index,
            "conflict_policy": self.conflict_policy.value,
            "if_table_exists": self.if_table_exists,
            "unique_key_column": self.unique_key_column,
            "allow_unsafe_append": self.allow_unsafe_append,
        }


@dataclass
class BatchProgress:
    """Progresso di un singolo batch durante la scrittura.

    Usato per aggiornare il progresso in Redis durante l'esecuzione asincrona.
    """
    batch_number: int
    total_batches: int
    rows_written: int
    total_rows: int
    elapsed_seconds: float = 0.0

    @property
    def percent(self) -> float:
        """Percentuale di completamento (0-100)."""
        if self.total_rows == 0:
            return 100.0
        return round((self.rows_written / self.total_rows) * 100, 1)

    @property
    def message(self) -> str:
        """Messaggio leggibile per il frontend."""
        return (
            f"Batch {self.batch_number}/{self.total_batches} — "
            f"{self.rows_written}/{self.total_rows} righe "
            f"({self.percent}%)"
        )


@dataclass
class ConflictDetail:
    """Dettaglio di un singolo conflitto rilevato nel pre-check.

    Attributes:
        row_index: indice della riga nel DataFrame sorgente
        match_values: valori delle colonne match che causano il conflitto
        existing_values: valori corrispondenti nel record esistente (se disponibili)
        conflict_type: tipo conflitto ("pk_duplicate", "unique_violation")
    """
    row_index: int
    match_values: Dict[str, Any] = field(default_factory=dict)
    existing_values: Dict[str, Any] = field(default_factory=dict)
    conflict_type: str = "pk_duplicate"


@dataclass
class ConflictReport:
    """Report completo dei conflitti rilevati nel pre-check APPEND.

    Il pre-check è in 2 passaggi:
    1. Verifica duplicati interni nel DataFrame sorgente
    2. Verifica conflitti con dati già presenti nella tabella destinazione

    Attributes:
        has_conflicts: True se ci sono conflitti
        total_conflicts: numero totale di righe in conflitto
        internal_duplicates: duplicati interni al DataFrame
        db_conflicts: conflitti con record esistenti nel DB
        details: primi N dettagli (max 100 per non appesantire la risposta)
        checked_at: timestamp del check
    """
    has_conflicts: bool = False
    total_conflicts: int = 0
    internal_duplicates: int = 0
    db_conflicts: int = 0
    details: List[ConflictDetail] = field(default_factory=list)
    checked_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_conflicts": self.has_conflicts,
            "total_conflicts": self.total_conflicts,
            "internal_duplicates": self.internal_duplicates,
            "db_conflicts": self.db_conflicts,
            "details": [
                {
                    "row_index": d.row_index,
                    "match_values": d.match_values,
                    "existing_values": d.existing_values,
                    "conflict_type": d.conflict_type,
                }
                for d in self.details[:100]  # max 100
            ],
            "checked_at": self.checked_at,
        }


@dataclass
class WriteResult:
    """Risultato finale dell'operazione CARICA.

    Contiene tutte le informazioni necessarie per il frontend (stato,
    righe scritte, errori) e per il CRON (return code consumabile).

    Attributes:
        status: stato finale (SUCCESS, PARTIAL, CONFLICT, *_ERROR)
        rows_written: numero righe effettivamente scritte
        rows_failed: numero righe fallite
        rows_skipped: numero righe skippate (conflitti con policy SKIP)
        total_rows: numero righe totali nel DataFrame sorgente
        table_name: nome tabella destinazione (con schema)
        write_mode: modalità di scrittura utilizzata
        error_message: messaggio errore (se presente)
        error_details: dettagli tecnici errore (stacktrace, query fallita)
        conflict_report: report conflitti (se modalità APPEND)
        started_at: timestamp inizio operazione
        completed_at: timestamp fine operazione
        batches_completed: numero
        batches_total: numero
    """
    status: WriteStatus = WriteStatus.SUCCESS
    rows_written: int = 0
    rows_failed: int = 0
    rows_skipped: int = 0
    total_rows: int = 0
    table_name: str = ""
    write_mode: str = ""
    error_message: str = ""
    error_details: str = ""
    conflict_report: Optional[ConflictReport] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    batches_completed: int = 0
    batches_total: int = 0

    @property
    def success_rate(self) -> float:
        """Percentuale di righe scritte con successo."""
        if self.total_rows == 0:
            return 100.0
        return round((self.rows_written / self.total_rows) * 100, 1)

    @property
    def duration_seconds(self) -> Optional[float]:
        """Durata dell'operazione in secondi."""
        if not self.started_at or not self.completed_at:
            return None
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.completed_at)
            return (end - start).total_seconds()
        except (ValueError, TypeError):
            return None

    def to_dict(self) -> Dict[str, Any]:
        """Serializza per la risposta API e il logging."""
        result = {
            "status": self.status.value,
            "rows_written": self.rows_written,
            "rows_failed": self.rows_failed,
            "rows_skipped": self.rows_skipped,
            "total_rows": self.total_rows,
            "success_rate": self.success_rate,
            "table_name": self.table_name,
            "write_mode": self.write_mode,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "batches_completed": self.batches_completed,
            "batches_total": self.batches_total,
        }
        if self.error_message:
            result["error_message"] = self.error_message
        if self.error_details:
            result["error_details"] = self.error_details
        if self.conflict_report:
            result["conflict_report"] = self.conflict_report.to_dict()
        return result


@dataclass
class AuditRecord:
    """Record di audit per logging permanente su filesystem.

    Ogni operazione CARICA produce un AuditRecord salvato come JSON
    nella directory logs/carica/. Usato per tracciare chi ha scritto
    cosa, quando, e con quale esito.

    Formato filename: carica_{timestamp}_{table}_{status}.json
    """
    timestamp: str = ""
    project_id: str = ""
    connection_id: str = ""
    schema_name: str = ""
    table_name: str = ""
    write_mode: str = ""
    total_rows: int = 0
    rows_written: int = 0
    rows_failed: int = 0
    rows_skipped: int = 0
    status: str = ""
    error_message: str = ""
    duration_seconds: Optional[float] = None
    batch_size: int = 0
    transaction_mode: str = ""
    geometry_srid: Optional[int] = None
    triggered_by: str = "manual"  # "manual" | "cron"

    @staticmethod
    def from_write_result(
        result: WriteResult,
        config: WriteConfig,
        project_id: str = "",
        triggered_by: str = "manual"
    ) -> "AuditRecord":
        """Crea AuditRecord dal risultato di un'operazione."""
        return AuditRecord(
            timestamp=result.started_at or datetime.now(timezone.utc).isoformat(),
            project_id=project_id,
            connection_id=config.connection_id,
            schema_name=config.schema_name,
            table_name=config.table_name,
            write_mode=config.write_mode.value,
            total_rows=result.total_rows,
            rows_written=result.rows_written,
            rows_failed=result.rows_failed,
            rows_skipped=result.rows_skipped,
            status=result.status.value,
            error_message=result.error_message,
            duration_seconds=result.duration_seconds,
            batch_size=config.batch_size,
            transaction_mode=config.transaction_mode.value,
            geometry_srid=config.geometry_srid,
            triggered_by=triggered_by,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "project_id": self.project_id,
            "connection_id": self.connection_id,
            "schema_name": self.schema_name,
            "table_name": self.table_name,
            "write_mode": self.write_mode,
            "total_rows": self.total_rows,
            "rows_written": self.rows_written,
            "rows_failed": self.rows_failed,
            "rows_skipped": self.rows_skipped,
            "status": self.status,
            "error_message": self.error_message,
            "duration_seconds": self.duration_seconds,
            "batch_size": self.batch_size,
            "transaction_mode": self.transaction_mode,
            "geometry_srid": self.geometry_srid,
            "triggered_by": self.triggered_by,
        }
