"""
Eccezioni custom condivise tra i moduli backend.
"""

from typing import List, Dict, Any, Literal, Optional


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


class CanvasCorruptedError(Exception):
    """Sollevata quando il canvas di un progetto e' presente in DB ma non
    interpretabile: JSON malformato, tipo inatteso, schema Pydantic
    non valido.

    Motivazione (reperto: silent-fallback a CanvasState vuoto):
      Fino al fix, `get_project_canvas` ritornava un canvas vuoto in
      caso di corruzione, loggando solo un warning. Conseguenze:
        - L'utente apriva il progetto, vedeva canvas vuoto, lo salvava
          sovrapposto al contenuto corrotto originale → perdita dati
          definitiva.
        - Il warning nel log era facilmente perso, nessun alert al caller.
        - Impossibile distinguere "progetto nuovo senza canvas" da
          "progetto con canvas corrotto".

    Ora il servizio solleva esplicitamente. Il router traduce in
    HTTP 500 con messaggio leggibile, cosi' l'utente sa che c'e' un
    problema e puo' chiedere il ripristino da backup.

    Attributi:
        project_id: id del progetto col canvas corrotto
        reason: descrizione umana del problema (JSON invalido, tipo,
            schema). Safe da esporre in risposta HTTP: non contiene
            dati utente sensibili.
        original_error: eccezione sottostante (json.JSONDecodeError,
            ValidationError, ecc.) per logging strutturato.
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
            f"Canvas del progetto '{project_id}' corrotto: {reason}"
        )


class ProjectHasDependenciesError(Exception):
    """Sollevata quando si tenta di eliminare un progetto che ha entità collegate
    che impediscono la cancellazione.

    Tipi di blocco supportati:
      - eseguibili pubblicati a partire dal progetto
      - altri progetti (manipola/arricchisci) che lo usano come sorgente

    Attributi:
        project_id: id del progetto che non può essere eliminato
        eseguibili: lista di dict {id, name} degli eseguibili bloccanti
        dependent_projects: lista di dict {id, name, project_type} dei progetti
            che referenziano questo come sorgente
    """

    def __init__(
        self,
        project_id: str,
        eseguibili: List[Dict[str, Any]] = None,
        dependent_projects: List[Dict[str, Any]] = None,
    ):
        self.project_id = project_id
        self.eseguibili = eseguibili or []
        self.dependent_projects = dependent_projects or []
        parts = []
        if self.eseguibili:
            parts.append(f"{len(self.eseguibili)} eseguibile/i")
        if self.dependent_projects:
            parts.append(f"{len(self.dependent_projects)} progetto/i dipendenti")
        deps = " e ".join(parts) if parts else "dipendenze"
        super().__init__(
            f"Progetto '{project_id}' ha {deps} collegati: "
            f"rimuoverli prima di cancellare il progetto."
        )


class ProjectCleanupPartialError(Exception):
    """Sollevata quando la cascade cleanup in `delete_project` fallisce su
    almeno uno step classificato come CRITICO (intermediate_runs o
    results_blobs). La row `config.projects` NON viene rimossa: invariante
    fail-closed "o progetto cancellato pulito, o ancora presente".

    Contesto (reperto S1 Batch 9, audit `error_handling_batch9_orchestrator_cleanup_audit.md`):
      Pre-fix, `delete_project` aveva cascade a 5 step con 4 `except Exception:
      warning + continue`; lo step 5 (config DELETE) veniva eseguito SEMPRE,
      anche se intermediate runs / results blobs erano orfani cross-DB. La
      row in `config.projects` spariva e il caller vedeva `{"status":
      "deleted"}`: il claim "cancellato" era silenziosamente violato,
      compensato solo dal job admin `orphan_cleanup_service`.

      Fix C5 ha introdotto l'accumulo `critical_failures` e un gate pre-
      step 5 che sollevava `RuntimeError` con messaggio strutturato.
      Fix C6 (questo) promuove la segnalazione a classe tipizzata cosi'
      che il caller `projects/router.py` possa catturarla via isinstance
      e mappare a `HTTPException(500)` con shape JSON leggibile sia da
      umano (FE toast) sia da tool (monitoring/alerting).

    Step critici (falliscono → fail-closed):
      - `intermediate_runs`: `app.core.intermediate.db.delete_all_runs_for_canvas`
      - `results_blobs`: `app.db.results_db_service.delete_results_for_project`

    Step derivati (falliscono → tolerati, non bloccano):
      - `schedules_orphan`: `ScheduleService.mark_as_orphan`
      - `comparison_keys`: `app.db.config_db_service.delete_comparison_keys`

    Attributi:
        project_id: id del progetto per cui la cascade e' fallita.
        critical_failed: lista nomi canonici step critici falliti (non-vuota
            per definizione: l'eccezione si solleva solo se almeno uno
            critical ha fallito).
        derived_failed: lista nomi canonici step derivati falliti, riportati
            per diagnosticita'. Puo' essere vuota.
    """

    def __init__(
        self,
        project_id: str,
        critical_failed: List[str],
        derived_failed: Optional[List[str]] = None,
    ):
        self.project_id = project_id
        self.critical_failed = list(critical_failed)
        self.derived_failed = list(derived_failed or [])
        super().__init__(
            f"Cleanup parziale del progetto '{project_id}': "
            f"step critici falliti {self.critical_failed}. "
            f"Il progetto non e' stato cancellato."
        )


class IntermediateWriteError(Exception):
    """Sollevata quando la scrittura intermediate di un nodo fallisce.

    Contesto (reperto S2 Batch 8, audit `error_handling_batch8_storage_provider_audit.md`):
      `intermediate/writer.write_node` e' chiamato dagli executor
      (estrai/manipola/arricchisci) a fine nodo per persistere righe e
      metadata nella intermediate.db (PG plenora schema). Pre-fix un
      fallimento ritornava silenziosamente `sha256(b"error").hexdigest()`:
      un hash sintatticamente valido (64 hex chars) ma semanticamente
      fake. Indistinguibile da un hash reale a valle → `input_hash` e
      `output_hash` del run potevano contenere marker "error" invisibile,
      violando l'invariante di determinismo di dedup/replay.

    Post-fix (questa eccezione):
      - Il fake hash NON viene piu' ritornato.
      - `write_node` solleva questa eccezione dopo aver tentato il fallback
        `upsert_node_meta(status="error")` come resource guard.
      - I caller attuali (query_executor, manipola/executor, arricchisci/
        api_connector, enrichment_task) gia' wrappano `write_node` in
        `try/except Exception: logger.warning + pass` perche' il contratto
        di intermediate e' "best-effort non bloccante": l'eccezione viene
        swallowed lato caller. Effetto macroscopico invariato, ma invariante
        di determinismo preservato.
      - Caller futuri che vogliono differenziare "intermediate non critico"
        da "pipeline primaria fallita" possono fare `except
        IntermediateWriteError` invece di `except Exception` broad.

    Attributi:
        run_id: id della run intermediate su cui il write ha fallito.
        node_id: id del nodo che ha fallito la scrittura.
        original_error: eccezione sottostante (tipicamente psycopg2.Error,
            OSError, ecc.). Usata per logging strutturato + preservazione
            chain traceback via `raise ... from original_error`. Non va
            esposta via response API.
    """

    def __init__(
        self,
        run_id: str,
        node_id: str,
        original_error: Optional[Exception] = None,
    ):
        self.run_id = run_id
        self.node_id = node_id
        self.original_error = original_error
        err_type = (
            type(original_error).__name__ if original_error else "unknown"
        )
        super().__init__(
            f"Intermediate write fallito per run={run_id} node={node_id} "
            f"({err_type})"
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
            lettura progetto dal DB"). Non contiene str() dell'errore
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
            lettura file_source dal DB"). Non contiene str() dell'errore
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
