"""
Eccezioni custom condivise tra i moduli backend.
"""

from typing import List, Dict, Any, Literal, Optional

from app.core.intermediate.exceptions import IntermediateWriteError
from app.core.projects.exceptions import (
    CanvasCorruptedError,
    ProjectCleanupPartialError,
    ProjectHasDependenciesError,
)


class ColumnsMetadataSaveError(Exception):
    """Sollevata quando il save di `columns_metadata` sul canvas fallisce
    in modo non-recuperabile dopo i retry transient.

    Contesto (reperto: fail silenzioso su save metadati tipi ESTRAI):
      Gli executor ESTRAI/MANIPOLA/ARRICCHISCI persistono tipi colonna
      in `projects.canvas.columns_metadata` a fine esecuzione. Questo
      dato e' contratto FORTE: il hook N11 (canvas_types_enricher) e
      gli executor downstream (manipola/carica come project_source)
      leggono da qui i tipi per propagarli. Senza metadata coerenti
      i tipi cadono a fallback `str` → numeri/date diventano testo.

      Pre-fix, un save fallito era degradato a logger.warning — il
      task tornava SUCCESS con blob persistito ma metadata stale. Il
      lineage downstream si contaminava silenziosamente.

    Post-fix (questa eccezione):
      - Il save ha retry selettivo su errori transient (DB pool
        exhausted, deadlock, lock wait timeout, network blip).
      - Dopo N retry, o su errori non-transient, il task solleva e
        Celery lo marca FAILED con messaggio esplicito.
      - L'utente sa che: (a) il risultato e' gia' persistito, (b) i
        metadata sono disallineati, (c) rilanciare il task li ricrea.

    Attributi:
        project_id: id del progetto per cui il save e' fallito
        reason: descrizione umana dell'errore sottostante
        attempts: quanti tentativi sono stati fatti (per log/debug)
        result_already_persisted: True se il blob risultato e' gia'
            stato scritto (execute_query ha committato prima del save
            metadati) → l'utente puo' scaricarlo anche se lineage
            downstream non e' allineato.
    """

    def __init__(
        self,
        project_id: str,
        reason: str,
        attempts: int = 1,
        result_already_persisted: bool = True,
    ):
        self.project_id = project_id
        self.reason = reason
        self.attempts = attempts
        self.result_already_persisted = result_already_persisted

        persisted_note = (
            "Il risultato (blob) e' gia' salvato nel DB e puo' essere "
            "scaricato. "
            if result_already_persisted
            else ""
        )
        super().__init__(
            f"Save columns_metadata fallito per progetto '{project_id}' "
            f"dopo {attempts} tentativi: {reason}. "
            f"{persisted_note}"
            f"I tipi colonna NON sono stati aggiornati: il lineage "
            f"downstream (manipola/carica che usano questo progetto "
            f"come source) potrebbe usare fallback 'str' invece dei "
            f"tipi reali. Rilanciare l'esecuzione per ricreare i metadata."
        )




class ExecutionContextError(Exception):
    """Sollevata quando l'inizializzazione del contesto di esecuzione
    per `arricchisci/api_connector.execute_enrichment` fallisce su
    errore DB durante il lookup del progetto.

    Contesto (reperto: fail-soft silenzioso su bootstrap arricchisci):
      `_init_execution_context` esegue `db_svc.get_project(project_id)`
      per ricavare `group_id` (tenant binding usato dai check
      cross-tenant in `_load_source_project`, `_load_file_source`,
      `_resolve_file_source_path`, `_assert_file_source_group_access`).

      Pre-fix, un errore DB durante il lookup era degradato a
      `logger.warning + _current_group_id = None`, delegando il
      fail-closed ai check downstream. Il comportamento finale era
      corretto (i check downstream rifiutano `group_id=None` con
      "Contesto di sicurezza non determinabile") ma mascherava bug
      infrastrutturali (pool exhausted, DB down, tabella mancante
      post-migrazione) dietro un messaggio indistinguibile da
      "progetto non esistente" — ramo dati legittimo.

    Post-fix (questa eccezione):
      - Su errore DB classificato (`psycopg2.Error`), il contesto
        solleva `ExecutionContextError` con chain preservata via
        `from e`.
      - Il boundary di `execute_enrichment` (Fix-C gia' mergiato) la
        cattura nel ramo `except Exception` e la traduce in response
        `{success:False, user_error:False, error:"Errore interno
        durante l'arricchimento"}`. Dettaglio tecnico solo nei log.
      - Il comportamento su `_proj_row is None` (progetto inesistente)
        resta invariato: `_current_group_id = None` + fail-closed
        downstream. E' ramo dati, non errore.

    Attributi:
        project_id: id del progetto su cui l'init stava operando.
        reason: descrizione safe-to-show del problema (es. "errore
            lettura progetto dal DB"). Non contiene str dell'errore
            originale: il dettaglio tecnico resta nell'attributo
            `original_error` e nei log, non in questo messaggio.
        original_error: eccezione sottostante (tipicamente
            `psycopg2.Error` o sottoclasse). Usata per logging
            strutturato + preservazione chain (`raise ... from e`).
            Non va esposta al caller via response API.
    """

    def __init__(
        self,
        project_id: str,
        reason: str,
        original_error: Optional[Exception] = None,
    ):
        self.project_id = project_id
        self.reason = reason
        self.original_error = original_error
        super().__init__(
            f"Errore inizializzazione contesto esecuzione per "
            f"progetto '{project_id}': {reason}"
        )


class FileSourceResolutionError(Exception):
    """Sollevata quando la materializzazione di un file_source su
    filesystem fallisce per errore tecnico (lookup DB o scrittura cache),
    distinto dal caso "file_source inesistente / blob vuoto" che resta
    `return None` del caller.

    Contesto (reperto: broad-catch in `_resolve_file_source_id_path`):
      `ApiConnectionService._resolve_file_source_id_path` materializza
      il blob di un file_source dal DB a una cache locale
      (`{FILES_DIR}/{file_source_id}/{filename}`) perche' fiona/GDAL
      lavorano con path fisici. L'operazione fa due query DB
      (`get_file_source` con e senza `include_data`) e una scrittura FS
      atomica (`.tmp` + `os.replace`).

      Pre-fix, l'intero try era protetto da `except Exception ->
      logger.warning + return None`. Questo conflato:
        - errori DB (pool exhausted, connection drop) -> None
        - errori FS (filesystem read-only, permission denied) -> None
        - file_source inesistente (`row_meta is None`) -> None
        - blob vuoto (`row_full["data"] is None`) -> None
      I caller (`_resolve_file_source_path`, `_build_rest_file_multipart`
      ramo extra_input) interpretavano `None` come "file non
      disponibile" e producevano messaggi tipo "file_source 'X' non
      trovato" o "non risolvibile, saltato". Un errore DB transient
      appariva all'utente identico a un file_source mai configurato.
      Sul ramo optional (`required=False`), il task proseguiva
      silenziosamente saltando il file -- fail-open non ratificato.

    Post-fix (questa eccezione):
      - Errori DB classificati (`psycopg2.Error`) -> raise
        `FileSourceResolutionError(cause="lookup", ...)` con chain
        preservata via `from e`.
      - Errori FS (`OSError`, include `PermissionError`) -> raise
        `FileSourceResolutionError(cause="materialization", ...)`.
      - `row_meta is None` / `row_full["data"] is None` -> return `None`
        (ramo dati legittimo, invariato).
      - Il boundary `_execute_rest_file` ramo input resolve (post
        Fix-D) cattura `FileSourceResolutionError` e la traduce in
        response `{success:False, error:"Risoluzione input fallita: ..."}`
        con messaggio domain safe-to-show. Path non-rest_file (generator,
        api_enrich) fluiscono al boundary `execute_enrichment` top-level
        (Fix-C gia' mergiato) e producono safe message generico.

    Fail-open NON ratificato:
      Anche nel ramo `extra_input` con `required=False`, un errore DB
      qui non viene piu' mascherato da `continue`: l'eccezione propaga
      e fa fallire il task esplicitamente. Scelta consapevole
      (errori tecnici fail-closed a prescindere dal flag required del
      campo).

    Attributi:
        file_source_id: id del file_source su cui l'operazione ha
            fallito. Safe da esporre (e' un UUID del DB).
        cause: `"lookup"` se l'errore e' sul lato DB,
            `"materialization"` se sul lato filesystem. Usato per la
            diagnostica, non per decidere il comportamento del caller
            (entrambi sono fail-closed).
        reason: descrizione safe-to-show del problema (es. "errore
            lettura file_source dal DB"). Non contiene str dell'errore
            originale: il dettaglio tecnico resta nell'attributo
            `original_error` e nei log, non in questo messaggio.
        original_error: eccezione sottostante (`psycopg2.Error` o
            sottoclasse, `OSError`, ecc.). Usata per logging
            strutturato + preservazione chain (`raise ... from e`).
            Non va esposta al caller via response API.
    """

    def __init__(
        self,
        file_source_id: str,
        cause: Literal["lookup", "materialization"],
        reason: str,
        original_error: Optional[Exception] = None,
    ):
        self.file_source_id = file_source_id
        self.cause = cause
        self.reason = reason
        self.original_error = original_error
        super().__init__(
            f"Errore risoluzione file_source '{file_source_id}' "
            f"({cause}): {reason}"
        )


class MemoryLimitExceededError(Exception):
    """Sollevata quando un input supera il limite di memoria
    configurato per uno stage di processing prima di
    materializzare il risultato.

        Prima di caricare un file via `load_dataframe_from_file`,
        un preflight guard confronta `file_path.stat.st_size`
        con `settings.LOAD_FILE_MAX_BYTES` (default 200 MB). Se il
        file supera il cap, il guard solleva questa eccezione
        invece di tentare un parse Pandas/GeoPandas che potrebbe
        portare il worker a OOM kernel SIGKILL.

    Convenzioni:
    - L'eccezione e' PURA (zero dipendenze FastAPI / starlette).
      Non importa moduli web — un caller in worker Celery o
      script CLI puo' catturarla allo stesso modo del caller HTTP.
    - Il mapping HTTP 413 vive nel boundary handler in
      `app/main.py`.
    - `path_basename` (NON path assoluto) per evitare leak di
      filesystem layout nella response al client.

    Attributi:
        observed_bytes: dimensione effettiva osservata del file (>=0).
        limit_bytes: cap configurato (`settings.LOAD_FILE_MAX_BYTES`).
        path_basename: basename del file (no path) per logging/UX
            senza leak filesystem.
        stage: identificatore dello stage di processing
            (es. `"load_dataframe_from_file"`). Permette di
            estendere il guard ad altri stage senza nuovi codici.
    """

    def __init__(
        self,
        observed_bytes: int,
        limit_bytes: int,
        path_basename: str,
        stage: str,
    ) -> None:
        self.observed_bytes = int(observed_bytes)
        self.limit_bytes = int(limit_bytes)
        self.path_basename = str(path_basename or "")
        self.stage = str(stage or "")
        super().__init__(
            f"Memory limit exceeded in stage '{self.stage}' for "
            f"'{self.path_basename}': observed={self.observed_bytes} "
            f"bytes, limit={self.limit_bytes} bytes."
        )
