"""
Eccezioni custom condivise tra i moduli backend.
"""

from typing import List, Dict, Any, Optional


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
