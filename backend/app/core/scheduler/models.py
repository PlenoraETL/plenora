"""
Modelli dati Pydantic per il modulo SCHEDULER.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

from pydantic import BaseModel, Field, model_validator
from typing import Any, Dict, List, Optional, Literal
from datetime import datetime


# TIPI E COSTANTI

# Stati possibili per uno schedule
ScheduleStatus = Literal[
    "idle",         # ⚪ In attesa del prossimo trigger
    "running",      # 🔵 Pipeline in esecuzione
    "queued",       # 🟡 In coda (precedente in corso
    "completed",    # 🟢 Ultimo run OK
    "partial",      # 🟠 Ultimo run con warning (righe skipped
    "failed",       # 🔴 Ultimo run fallito
    "disabled",     # ⏸️ Disabilitato dall'utente
    "orphan"        # 💀 Progetto eliminato, schedule disabilitato
]

# Eventi log per lo scheduler
ScheduleEventType = Literal[
    "SCHEDULE_TRIGGERED",       # Cron/manuale scatta
    "SCHEDULE_QUEUED",          # In coda
    "SCHEDULE_SKIPPED",         # Coda piena
    "PRE_CHECK_PASSED",         # Connessione DB OK
    "PRE_CHECK_FAILED",         # Connessione fallita, skip
    "PIPELINE_STARTED",         # Inizio esecuzione
    "PIPELINE_COMPLETED",       # Completata con successo
    "PIPELINE_PARTIAL",         # Completata con righe skipped
    "PIPELINE_FAILED",          # Fallita
    "RETRY_SCHEDULED",          # Retry programmato
    "RETRY_EXHAUSTED"           # Max retry raggiunto
]

# Livelli di log
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


# CONFIGURAZIONE SCHEDULE

class ScheduleFrequency(BaseModel):
    """
    Configurazione della frequenza di esecuzione.

    Supporta preset comuni e cron expression custom.
    Il timezone di default è Europe/Rome per il contesto ICSC.

    Catchup (C3 fix 3): opt-in per schedule. Se `catchup_missed_runs`
    e' True, il beat scheduler recupera al massimo un run mancante per
    tick (distribuito nel tempo su tick successivi in caso di backlog),
    entro la finestra `catchup_grace_hours`. Default off per
    retrocompat: gli schedule esistenti mantengono la semantica cron
    standard "missed is lost".
    """
    cron_expression: str                # Es. "0 3 * * *"
    timezone: str = "Europe/Rome"
    description: str = ""               # Es. "Ogni notte alle 03:00"
    enabled: bool = True
    catchup_missed_runs: bool = False
    catchup_grace_hours: int = Field(default=24, gt=0)


class ScheduleExecution(BaseModel):
    """
    Modalità di esecuzione della pipeline.

    use_cache=False (default): riesegue tutta la catena dalle sorgenti.
    use_cache=True: usa i dati già elaborati se disponibili.
    """
    use_cache: bool = False


class ScheduleDestination(BaseModel):
    """
    Destinazione opzionale per il caricamento su database (CARICA).

    Se presente, dopo l'esecuzione della pipeline e il salvataggio file,
    i dati vengono scritti sulla tabella database specificata.
    Se null/assente, lo scheduler salva solo il file output.

    I campi rispecchiano la configurazione del nodo CARICA nel canvas.
    """
    connection_id: str                  # ID connessione database (riusa quelle di ESTRAI
    schema_name: Optional[str] = None   # Schema destinazione (public, dbo, afm
    table_name: str                     # Nome tabella destinazione
    write_mode: Literal[
        "create", "replace", "append", "upsert", "update", "truncate_insert"
    ] = "create"
    match_columns: List[str] = []       # Colonne per UPSERT/UPDATE match
    update_columns: List[str] = []      # Colonne da aggiornare (vuoto = tutte
    geometry_srid: Optional[int] = None # SRID per colonne geometriche
    batch_size: int = 1000              # Righe per batch


class ScheduleConcurrency(BaseModel):
    """
    Gestione della concorrenza: cosa fare se il trigger scatta
    ma l'esecuzione precedente è ancora in corso.

    Policy "queue": metti in coda e parti appena la precedente finisce.
    max_queue_size: numero massimo di esecuzioni in coda (oltre → skip).
    """
    policy: Literal["queue"] = "queue"
    max_queue_size: int = 3             # Modifica anche ScheduleCreate.concurrency_max_queue se cambi


class SchedulePreCheck(BaseModel):
    """
    Pre-check connessione database prima di ricalcolare la pipeline.

    Evita di rieseguire tutta la catena ESTRAI → MANIPOLA → ARRICCHISCI
    per poi fallire alla fine perché il DB di destinazione è offline.
    Attivo solo se destination è configurata.
    """
    verify_destination_connection: bool = True
    timeout_seconds: int = 30


class ScheduleErrorPolicy(BaseModel):
    """
    Gestione errori automatica senza intervento utente.

    Retry con backoff esponenziale: il file output viene comunque salvato
    (la copia di sicurezza c'è sempre), solo CARICA può fallire.

    retry_backoff_seconds: attesa tra i tentativi [60, 300, 900] = 1min, 5min, 15min
    """
    max_retries: int = 3
    retry_backoff_seconds: List[int] = [60, 300, 900]


class ScheduleConflictPolicy(BaseModel):
    """
    Policy per conflitti in modalità APPEND/UPSERT.

    In esecuzione automatica non c'è nessuno a decidere, quindi
    la policy viene preconfigurata dall'utente nel wizard.

    - skip: inserisci solo le righe senza conflitti
    - force: sovrascrivi le righe in conflitto
    - abort: interrompi e segnala errore
    """
    append_conflicts: Literal["skip", "force", "abort"] = "skip"
    log_skipped_rows: bool = True


class LastExecution(BaseModel):
    """Informazioni sull'ultima esecuzione dello schedule."""
    timestamp: datetime
    status: Literal["completed", "partial", "failed"]
    rows_written: int = 0
    duration_ms: int = 0
    log_file: str = ""                  # Percorso relativo al log (es. "2026-02-07/sched_abc_030000.json"


class ScheduleRuntime(BaseModel):
    """
    Stato runtime dello schedule (aggiornato ad ogni esecuzione).

    Non configurabile dall'utente — gestito internamente dallo scheduler.
    """
    status: ScheduleStatus = "idle"
    last_execution: Optional[LastExecution] = None
    next_execution: Optional[datetime] = None
    total_runs: int = 0
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    current_task_id: Optional[str] = None


class SchedulePipeline(BaseModel):
    """
    Riferimento alla pipeline da eseguire.

    Lo scheduler legge le configurazioni di output (nome file, formato,
    GROUP BY, colonne) direttamente dal canvas del progetto salvato.
    Nessuna duplicazione — se l'utente modifica il progetto,
    il prossimo run usa automaticamente le nuove config.
    """
    project_id: str
    project_name: str = ""              # Snapshot del nome al momento della creazione
    project_type: Literal["estrai", "manipola", "arricchisci"] = "estrai"


# SCHEDULE CONFIG — Modello principale

class ScheduleConfig(BaseModel):
    """
    Configurazione completa di uno schedule.

    Entità separata dal progetto, persistita in data/schedules/{id}.json.
    Un progetto può avere più schedule con configurazioni diverse.
    Tutti i tipi di progetto sono schedulabili (ESTRAI, MANIPOLA, ARRICCHISCI).

    Lo schedule punta a un project_id e l'orchestrator headless
    riesegue automaticamente tutta la catena a monte.

    Se il progetto viene eliminato, lo schedule diventa "orphan"
    e viene disabilitato automaticamente.
    """
    id: str
    created_at: datetime
    updated_at: datetime

    # Riferimento alla pipeline
    pipeline: SchedulePipeline

    # Frequenza
    schedule: ScheduleFrequency

    # Modalità esecuzione
    execution: ScheduleExecution = ScheduleExecution()

    # Destinazione CARICA (opzionale — null = solo file
    destination: Optional[ScheduleDestination] = None

    # Policy
    concurrency: ScheduleConcurrency = ScheduleConcurrency()
    pre_check: SchedulePreCheck = SchedulePreCheck()
    on_error: ScheduleErrorPolicy = ScheduleErrorPolicy()
    conflict_policy: ScheduleConflictPolicy = ScheduleConflictPolicy()

    # Dipendenze (previste, non implementate v1
    depends_on_pipeline: Optional[str] = None

    # Stato runtime (gestito internamente
    runtime: ScheduleRuntime = ScheduleRuntime()


# CRUD — Richieste API

class ScheduleCreate(BaseModel):
    """
    Dati dal wizard di creazione (6 step).

    Step 1: pipeline (project_id)
    Step 2: schedule (cron_expression, timezone)
    Step 3: execution (use_cache)
    Step 4: destination (opzionale)
    Step 5: policy errori (retry, conflitti, concorrenza)
    Step 6: riepilogo → conferma
    """
    # Step 1
    project_id: str = Field(..., min_length=1, max_length=100)

    # Step 2
    cron_expression: str = Field(..., min_length=5, max_length=100)
    timezone: str = Field("Europe/Rome", max_length=50)
    description: str = Field("", max_length=2000)

    # Step 3
    use_cache: bool = False

    # Step 4 (opzionale) — accetta sia oggetto nested che campi flat dal frontend
    destination: Optional[ScheduleDestination] = None

    # Step 5
    max_retries: int = Field(3, ge=0, le=10)
    retry_backoff_seconds: List[int] = [60, 300, 900]
    append_conflicts: Literal["skip", "force", "abort"] = "skip"
    log_skipped_rows: bool = True
    concurrency_max_queue: int = Field(3, ge=1, le=20)

    # Campi flat opzionali dal frontend (usati dal validator sotto
    connection_id: Optional[str] = Field(None, max_length=100)
    schema_name: Optional[str] = Field(None, max_length=255)
    table_name: Optional[str] = Field(None, max_length=255)
    write_mode: Optional[str] = Field(None, max_length=50)
    batch_size: Optional[int] = Field(None, ge=1, le=100000)
    match_columns: Optional[List[str]] = None  # lista di nomi colonna

    @model_validator(mode="before")
    @classmethod
    def flatten_nested_payload(cls, data: Any) -> Any:
        """
        Backward-compat: il wizard frontend invia il payload in forma NESTED:
            {
              "project_id": "...",
              "schedule": {"cron_expression": "...", "timezone": "...", "description": "..."},
              "execution": {"use_cache": false},
              "destination": {...} o null,
              "on_error": {"max_retries": 3, "retry_backoff_seconds": [...], "append_conflicts": "skip", "log_skipped_rows": true},
              "concurrency": {"max_queue_size": 3}
            }
        Mentre il modello ScheduleCreate è FLAT. Questo validator appiattisce
        automaticamente i campi nested al top level prima della validazione.
        """
        if not isinstance(data, dict):
            return data

        # schedule.* → top level
        sched = data.get("schedule")
        if isinstance(sched, dict):
            for k in ("cron_expression", "timezone", "description"):
                if k in sched and k not in data:
                    data[k] = sched[k]

        # execution.* → top level
        exe = data.get("execution")
        if isinstance(exe, dict):
            if "use_cache" in exe and "use_cache" not in data:
                data["use_cache"] = exe["use_cache"]
            # concurrency_max_queue arriva da execution.concurrency_max_queue oppure
            # da concurrency.max_queue_size (i due wizard step
            if "concurrency_max_queue" in exe and "concurrency_max_queue" not in data:
                data["concurrency_max_queue"] = exe["concurrency_max_queue"]

        # on_error.* → top level
        on_err = data.get("on_error")
        if isinstance(on_err, dict):
            for k in ("max_retries", "retry_backoff_seconds", "append_conflicts", "log_skipped_rows"):
                if k in on_err and k not in data:
                    data[k] = on_err[k]

        # concurrency.max_queue_size → concurrency_max_queue (top level
        conc = data.get("concurrency")
        if isinstance(conc, dict) and "max_queue_size" in conc and "concurrency_max_queue" not in data:
            data["concurrency_max_queue"] = conc["max_queue_size"]

        return data

    @model_validator(mode="after")
    def build_destination_from_flat_fields(self) -> "ScheduleCreate":
        """
        Se il frontend manda i campi destination piatti (connection_id, table_name...),
        li avvolge nell'oggetto ScheduleDestination atteso dal service.
        """
        if self.destination is None and self.connection_id and self.table_name:
            # Normalizza match_columns: stringa → lista
            mc = self.match_columns or []
            if isinstance(mc, str):
                mc = [c.strip() for c in mc.split(",") if c.strip()]

            self.destination = ScheduleDestination(
                connection_id=self.connection_id,
                schema_name=self.schema_name or None,
                table_name=self.table_name,
                write_mode=self.write_mode or "append",
                batch_size=self.batch_size or 1000,
                match_columns=mc,
            )
        return self


class ScheduleUpdate(BaseModel):
    """
    Dati per la modifica (wizard precompilato, tutti i campi opzionali).

    Il wizard si riapre con i valori attuali, l'utente modifica
    solo gli step che vuole cambiare.
    """
    # Step 1
    project_id: Optional[str] = None

    # Step 2
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None
    description: Optional[str] = None

    # Step 3
    use_cache: Optional[bool] = None

    # Step 4
    destination: Optional[ScheduleDestination] = None
    clear_destination: bool = False     # True per rimuovere la destinazione

    # Step 5
    max_retries: Optional[int] = None
    retry_backoff_seconds: Optional[List[int]] = None
    append_conflicts: Optional[Literal["skip", "force", "abort"]] = None
    log_skipped_rows: Optional[bool] = None
    concurrency_max_queue: Optional[int] = None


class ScheduleList(BaseModel):
    """Wrapper per la lista di schedule."""
    schedules: List[ScheduleConfig]
    total: int = 0


class ScheduleToggle(BaseModel):
    """Richiesta per abilitare/disabilitare uno schedule."""
    enabled: bool


class ScheduleCatchupUpdate(BaseModel):
    """Richiesta per modificare il catchup config di uno schedule (C3 fix 3).

    Dedicato per pulizia API: evita di passare tutto il ScheduleConfig
    via update generico quando si vuole solo attivare/disattivare il
    catchup o aggiustare la grace window.
    """
    enabled: bool
    grace_hours: int = Field(default=24, gt=0)


# LOG ESECUZIONI

class LogStep(BaseModel):
    """
    Singolo step nell'esecuzione di una pipeline schedulata.

    Traccia ogni fase: ESTRAI, MANIPOLA, ARRICCHISCI, OUTPUT, CARICA.
    """
    step: Literal["ESTRAI", "MANIPOLA", "ARRICCHISCI", "OUTPUT", "CARICA"]
    status: Literal["success", "partial", "failed", "skipped"]
    duration_ms: int = 0
    rows: int = 0

    # Campi specifici per OUTPUT
    file: Optional[str] = None          # Nome file generato

    # Campi specifici per CARICA
    rows_inserted: Optional[int] = None
    rows_updated: Optional[int] = None
    rows_skipped: Optional[int] = None
    write_mode: Optional[str] = None
    destination: Optional[str] = None   # Es. "postgresql://catasto/immobili"


class ScheduleLogEntry(BaseModel):
    """
    Log strutturato di una singola esecuzione schedulata.

    Persistito su filesystem in data/logs/YYYY-MM-DD/sched_{id}_{HHMMSS}.json.
    Formato pensato per il futuro pannello log unificato (CARICA + SCHEDULER).
    """
    log_id: str                         # Es. "sched_abc123_20260207_030000"
    schedule_id: str
    timestamp: datetime
    level: LogLevel = "INFO"
    module: Literal["SCHEDULER"] = "SCHEDULER"
    event: ScheduleEventType
    triggered_by: Literal["cron", "manual"] = "cron"

    # Dettagli schedule
    schedule_info: Optional[Dict] = None    # cron_expression, scheduled_at, started_at, completed_at

    # Dettagli pipeline
    pipeline_info: Optional[Dict] = None    # project_id, project_name, project_type

    # Dettagli esecuzione
    execution_info: Optional[Dict] = None   # use_cache, pre_check_passed, retry_attempt, queue_wait_ms

    # Step della pipeline
    steps: List[LogStep] = []

    # Riepilogo
    total_duration_ms: int = 0
    error_message: Optional[str] = None


class ScheduleLogList(BaseModel):
    """Lista paginata di log esecuzioni."""
    logs: List[ScheduleLogEntry]
    total: int = 0
    page: int = 1
    page_size: int = 20


# HEALTHCHECK

class BeatHealthcheck(BaseModel):
    """
    Stato del processo Celery Beat.

    Verificato tramite heartbeat in Redis scritto da RedBeat.
    Se last_beat è più vecchio di 2 minuti → Beat è down.
    """
    status: Literal["ok", "warning", "down", "unknown"] = "unknown"
    last_beat: Optional[datetime] = None
    message: str = ""
