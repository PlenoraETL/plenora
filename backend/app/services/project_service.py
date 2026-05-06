"""
Servizio CRUD per progetti PLENORA-ETL.

Architettura
============
Questo servizio gestisce il ciclo di vita dei progetti.
Storage: PostgreSQL via app.db.db_service (niente filesystem per i metadati).

I file risultato (CSV, XLSX, GeoPackage) vengono salvati come blob
nella tabella `results` del DB.

Operazioni:
- list_projects(): lista tutti i progetti del gruppo
- get_project(): recupera metadati + canvas per ID
- create_project(): crea record progetto con canvas vuoto
- update_canvas(): aggiorna canvas JSON
- delete_project(): elimina progetto e risultati collegati
- save_result(): salva file output come blob nel DB
- get_result(): recupera blob per download

Tipi progetto: ESTRAI, MANIPOLA, ARRICCHISCI
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from app.utils.logger import get_logger
from pathlib import Path
from app.config import settings
from app.models import (
    ProjectMetadata,
    ProjectCreate,
    ProjectList,
    CanvasState,
)
import app.db.db_service as db

logger = get_logger(__name__)


class ProjectService:
    """
    Servizio CRUD per progetti PLENORA-ETL.

    Usa PostgreSQL come storage tramite db_service.
    group_id viene passato esplicitamente — in futuro verrà dal JWT.
    """

    DEFAULT_GROUP = settings.DEFAULT_GROUP  # configurabile in .env

    def _generate_project_id(self, name: str) -> str:
        """Genera ID univoco leggibile: {nome_safe}_{timestamp}_{uuid8}."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_")).strip()
        safe_name = safe_name.replace(" ", "_")[:30]
        return f"{safe_name}_{timestamp}_{short_uuid}"

    def _ensure_default_group(self):
        """Crea il gruppo default se non esiste (migrazione da filesystem)."""
        if not db.get_group(self.DEFAULT_GROUP):
            db.create_group(self.DEFAULT_GROUP, "Default", "Gruppo di default")

    def _row_to_metadata(self, row: dict) -> Optional[ProjectMetadata]:
        """Converte riga DB in ProjectMetadata."""
        if not row:
            return None
        return ProjectMetadata(
            id=row["id"],
            name=row["name"],
            description=row.get("description", ""),
            project_type=row.get("project_type", "estrai"),
            default_connection_id=row.get("connection_id"),
            group_id=row.get("group_id") or settings.DEFAULT_GROUP,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def create_project(self, project_data: ProjectCreate, group_id: str = None,
                       created_by: str = None) -> ProjectMetadata:
        """Crea nuovo progetto nel DB."""
        self._ensure_default_group()
        group_id = group_id or self.DEFAULT_GROUP
        project_id = self._generate_project_id(project_data.name)

        row = db.create_project(
            project_id=project_id,
            group_id=group_id,
            name=project_data.name,
            project_type=project_data.project_type,
            connection_id=project_data.default_connection_id,
            created_by=created_by,
        )
        logger.info(f"Progetto creato: {project_id} (created_by={created_by})", op="create_project")
        return self._row_to_metadata(row)

    def list_projects(self, group_id: str = None) -> ProjectList:
        """Lista progetti.

        - group_id=None → no filter (usato dal superadmin per vedere tutti i gruppi)
        - group_id="X"  → solo progetti del gruppo X
        """
        self._ensure_default_group()
        rows = db.list_projects(group_id)  # NB: il db.list_projects deve supportare None
        projects = [self._row_to_metadata(r) for r in rows if r]
        return ProjectList(projects=[p for p in projects if p])

    def get_project_metadata(self, project_id: str, group_id: str = None) -> Optional[ProjectMetadata]:
        """Carica metadati progetto.

        - group_id=None → no filter (qualsiasi gruppo); il check di accesso
          spetta al chiamante (es. assert_group_access nel router).
        - group_id="X"  → solo se appartiene al gruppo X.
        """
        row = db.get_project(project_id, group_id=group_id)
        return self._row_to_metadata(row)

    def get_project_canvas(self, project_id: str) -> Optional[CanvasState]:
        """Carica canvas del progetto.

        Ritorna:
          - `None` se il progetto non esiste.
          - `CanvasState()` vuoto se il canvas e' `None`/`{}` in DB
            (caso legittimo: progetto appena creato, mai salvato).

        Solleva `CanvasCorruptedError` se il canvas e' presente ma
        non interpretabile (JSON malformato, tipo inatteso, schema
        Pydantic non valido). Pre-fix questi casi ritornavano un
        canvas vuoto silenziosamente → l'utente sovrascriveva i dati
        originali senza accorgersene. Ora la corruzione e' fail-loud:
        il router traduce in 500 con messaggio esplicito e l'utente
        puo' chiedere il restore da backup.
        """
        from app.shared.exceptions import CanvasCorruptedError

        row = db.get_project(project_id)
        if not row:
            return None

        canvas_data = row.get("canvas")

        # Canvas mancante / vuoto → progetto nuovo, non corruzione.
        if canvas_data is None or canvas_data == {} or canvas_data == "":
            return CanvasState()

        # JSON string: tentativo di parse. Fallimento = corruzione.
        if isinstance(canvas_data, str):
            import json
            try:
                canvas_data = json.loads(canvas_data)
            except json.JSONDecodeError as e:
                logger.error(
                    f"Canvas JSON malformato per progetto {project_id}: {e}",
                    op="get_project_canvas",
                )
                raise CanvasCorruptedError(
                    project_id=project_id,
                    reason=f"JSON malformato ({e.msg} at line {e.lineno})",
                    original_error=e,
                )
            # Dopo decode: se e' il literal null/vuoto, canvas legittimo.
            if canvas_data is None or canvas_data == {}:
                return CanvasState()

        # Tipo inatteso dopo parse: non e' un dict ne' un valore vuoto
        # riconosciuto → strutturalmente corrotto.
        if not isinstance(canvas_data, dict):
            got_type = type(canvas_data).__name__
            logger.error(
                f"Canvas con tipo inatteso per {project_id}: {got_type}",
                op="get_project_canvas",
            )
            raise CanvasCorruptedError(
                project_id=project_id,
                reason=f"tipo inatteso '{got_type}', atteso dict",
            )

        # Schema Pydantic: eccezioni tipiche sono ValidationError (pydantic
        # v2) che eredita da ValueError. Gestiamo anche TypeError per
        # compat con altri modelli.
        try:
            return CanvasState(**canvas_data)
        except (TypeError, ValueError) as e:
            logger.error(
                f"Canvas con schema non valido per {project_id}: {e}",
                op="get_project_canvas",
            )
            # Tronchiamo il reason: ValidationError e' verboso.
            reason_str = str(e)
            if len(reason_str) > 300:
                reason_str = reason_str[:297] + "..."
            raise CanvasCorruptedError(
                project_id=project_id,
                reason=f"schema non valido ({reason_str})",
                original_error=e,
            )

    def get_project(self, project_id: str, group_id: str = None) -> Optional[dict]:
        """Carica progetto completo (metadata + canvas).

        - group_id=None → no filter (qualsiasi gruppo); il check di accesso
          spetta al chiamante.
        - group_id="X"  → solo se appartiene al gruppo X.
        """
        row = db.get_project(project_id, group_id=group_id)
        if not row:
            return None
        metadata = self._row_to_metadata(row)
        canvas = row.get("canvas", {"nodes": [], "edges": []})
        return {
            "metadata": metadata.model_dump() if metadata else {},
            "canvas": canvas,
            "id": project_id,
        }

    def update_canvas(self, project_id: str, canvas: CanvasState) -> bool:
        """Salva canvas e aggiorna updated_at. False se il progetto non esiste.

        Raises:
            ProjectLineageCycleError: se i `project_source` nel canvas +
                le dipendenze transitive degli altri progetti formano un
                ciclo (C3 fix 5). Il router traduce in HTTP 400.
        """
        row = db.get_project(project_id)
        if not row:
            return False

        self._assert_no_lineage_cycle(project_id, canvas)

        db.update_project(project_id, canvas=canvas.model_dump())
        return True

    def _assert_no_lineage_cycle(self, project_id: str, canvas: CanvasState) -> None:
        """Verifica che salvare `canvas` per `project_id` non introduca
        un ciclo nel DAG delle dipendenze cross-project (C3 fix 5).

        Costruisce una `get_sources_fn` che per il progetto corrente usa
        il CANVAS NUOVO (non ancora salvato) e per tutti gli altri legge
        il canvas dal DB. Cosi' il check anticipa la validazione al save
        invece di scoprire il ciclo solo al prossimo run.
        """
        from app.services.project_lineage import detect_project_lineage_cycle

        new_sources = [
            n.source_project_id
            for n in canvas.nodes
            if n.type == "project_source" and n.source_project_id
        ]

        def get_sources(pid: str):
            if pid == project_id:
                return new_sources
            row = db.get_project(pid)
            if not row:
                return []
            canvas_dict = row.get("canvas") or {}
            if isinstance(canvas_dict, str):
                import json
                # intent: optional feature — canvas corrotto non blocca cycle check
                # narrow difensivo: solo errori di parse/shape attesi; altro propaga fail-loud
                try:
                    canvas_dict = json.loads(canvas_dict)
                except (json.JSONDecodeError, TypeError, AttributeError) as e:
                    logger.warning(
                        f"canvas JSON malformato in cycle check per pid={pid}: {e}"
                    )
                    return []
            nodes = canvas_dict.get("nodes") or []
            return [
                n.get("source_project_id")
                for n in nodes
                if n.get("type") == "project_source" and n.get("source_project_id")
            ]

        chain = detect_project_lineage_cycle(project_id, get_sources)
        if chain is not None:
            from app.services.project_lineage import ProjectLineageCycleError
            raise ProjectLineageCycleError(chain)

    def _find_dependent_projects(self, project_id: str, group_id: str = None) -> List[dict]:
        """Trova progetti manipola/arricchisci che usano `project_id` come sorgente
        in un nodo `project_source` del proprio canvas.

        Cerca solo nello stesso gruppo (un progetto non può referenziare risorse
        di altri gruppi). Ritorna [{id, name, project_type}, ...].
        """
        # Recupera la riga del progetto per leggerne il group_id se non passato
        if group_id is None:
            row = db.get_project(project_id)
            if not row:
                return []
            group_id = row.get("group_id")

        # Fix N+1: fetch tutti i candidates con canvas in una sola query
        # invece di list + N get_project
        rows = db.fetchall(
            "SELECT id, name, project_type, canvas FROM projects "
            "WHERE group_id = %s AND project_type IN ('manipola', 'arricchisci')",
            (group_id,)
        )

        dependents = []
        for cand in rows:
            cand_id = cand.get("id")
            if not cand_id or cand_id == project_id:
                continue
            canvas = cand.get("canvas") or {}
            if isinstance(canvas, str):
                import json as _json
                # intent: optional feature — canvas corrotto del candidato non blocca la
                # scansione. Narrow difensivo come in _assert_no_lineage_cycle.get_sources.
                try:
                    canvas = _json.loads(canvas)
                except (_json.JSONDecodeError, TypeError, AttributeError) as e:
                    logger.warning(
                        f"canvas JSON malformato in _find_dependent_projects per candidato {cand_id}: {e}"
                    )
                    continue
            nodes = canvas.get("nodes") or [] if isinstance(canvas, dict) else []
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                if n.get("type") != "project_source":
                    continue
                node_data = n.get("data") or {}
                src = node_data.get("source_project_id") or n.get("source_project_id")
                if src == project_id:
                    dependents.append({
                        "id": cand_id,
                        "name": cand.get("name", cand_id),
                        "project_type": cand.get("project_type", ""),
                    })
                    break
        return dependents

    def delete_project(self, project_id: str, group_id: str = None) -> bool:
        """Elimina progetto e tutti i risultati collegati.

        - group_id=None → no filter (chiamante è superadmin o ha già fatto il check)
        - group_id="X"  → solo se appartiene al gruppo X

        Solleva ProjectHasDependenciesError se esistono eseguibili pubblicati
        a partire da questo progetto: vanno eliminati prima.
        """
        row = db.get_project(project_id, group_id=group_id)
        if not row:
            return False

        # Blocco: non eliminare progetti con eseguibili collegati o progetti
        # downstream che lo usano come sorgente. Si raccolgono entrambi e si
        # solleva un'unica eccezione per dare all'utente il quadro completo.
        from app.core.eseguibili.db import list_eseguibili_by_source
        from app.shared.exceptions import ProjectHasDependenciesError
        linked_eseguibili = list_eseguibili_by_source(project_id)
        dependent_projects = self._find_dependent_projects(
            project_id, group_id=row.get("group_id")
        )
        if linked_eseguibili or dependent_projects:
            raise ProjectHasDependenciesError(
                project_id,
                eseguibili=linked_eseguibili,
                dependent_projects=dependent_projects,
            )

        # Accumulatori cascade cleanup. `critical_failures` contiene step
        # che, se falliti, invalidano il claim "progetto cancellato" (orfani
        # cross-DB con dati utente). `derived_failures` contiene step il cui
        # fallimento e' tollerato (metadata/config derivata, recuperabile).
        # Se `critical_failures` e' non vuoto al termine, la row `config.projects`
        # NON viene rimossa (fail-closed) e si solleva RuntimeError con lista
        # step falliti: il caller deve ritentare o escalare.
        critical_failures: list[str] = []
        derived_failures: list[str] = []

        # intent: cascade step derived — mark_as_orphan e' idempotente, un
        # fallimento e' recuperabile via UI scheduler.
        # outcome: fail-open con breadcrumb (exc_info per diagnosi).
        # Non cancelliamo gli schedule: lo storico (log esecuzioni) resta
        # visibile e l'utente decide se rimuoverli manualmente dalla UI.
        try:
            from app.core.scheduler.schedule_service import ScheduleService
            schedule_service = ScheduleService()
            project_schedules = schedule_service.get_schedules_for_project(project_id)
            orphaned_count = 0
            for sched in project_schedules:
                if sched.runtime.status != "orphan":
                    schedule_service.mark_as_orphan(sched.id)
                    orphaned_count += 1
            if orphaned_count:
                logger.info(
                    f"Cleanup schedules: {orphaned_count} schedule marcati orphan per progetto {project_id}",
                    op="delete_project"
                )
        except Exception as e:
            logger.warning(
                f"Cleanup schedules fallito per {project_id}: {e}",
                op="delete_project",
                exc_info=True,
            )
            derived_failures.append("schedules_orphan")

        # intent: cascade step CRITICAL — intermediate tables + tabelle
        # dinamiche contengono dati utente (volume potenzialmente grande).
        # outcome: fail-closed via aggregazione (vedi gate sotto).
        try:
            from app.core.intermediate.db import delete_all_runs_for_canvas
            deleted_runs = delete_all_runs_for_canvas(project_id)
            if deleted_runs:
                logger.info(
                    f"Cleanup intermediate: {deleted_runs} run eliminate per progetto {project_id}",
                    op="delete_project"
                )
        except Exception as e:
            logger.error(
                f"[CRASH] Cleanup intermediate fallito per {project_id}: {e}",
                op="delete_project",
                exc_info=True,
            )
            critical_failures.append("intermediate_runs")

        # intent: cascade step CRITICAL — results tables/blob contengono output
        # utente esportati (GDPR-sensitive).
        # outcome: fail-closed via aggregazione.
        try:
            from app.db.results_db_service import delete_results_for_project
            deleted_results = delete_results_for_project(project_id)
            if deleted_results:
                logger.info(
                    f"Cleanup results: {deleted_results} blob eliminati per progetto {project_id}",
                    op="delete_project"
                )
        except Exception as e:
            logger.error(
                f"[CRASH] Cleanup results fallito per {project_id}: {e}",
                op="delete_project",
                exc_info=True,
            )
            critical_failures.append("results_blobs")

        # intent: cascade step derived — comparison_keys e' config derivata,
        # volume minimo e nessun dato utente sensibile. Orfano innocuo.
        # outcome: fail-open con breadcrumb.
        # Cleanup comparison_keys (config tables) — chiavi di confronto associate
        # ai nodi del canvas, identificate da canvas_id == project_id.
        try:
            from app.db.config.comparison_keys import delete_comparison_keys
            delete_comparison_keys(project_id)
        except Exception as e:
            logger.warning(
                f"Cleanup comparison_keys fallito per {project_id}: {e}",
                op="delete_project",
                exc_info=True,
            )
            derived_failures.append("comparison_keys")

        # Fail-closed gate: se uno step critical e' fallito, la row config
        # NON viene rimossa e si solleva ProjectCleanupPartialError. Questo
        # preserva l'invariante "o progetto cancellato pulito, o progetto
        # ancora presente", evitando che `config.projects` resti senza
        # orfani intermediate/results cross-DB (con dati utente residui).
        #
        # Step derivati falliti NON bloccano: vengono riportati sugli attributi
        # dell'eccezione per diagnostica ma non impediscono la cancellazione
        # quando solo derived hanno fallito. Il caller `projects/router.py`
        # cattura l'eccezione e mappa a HTTP 500 con shape strutturata
        # (Batch 9 C6).
        if critical_failures:
            logger.error(
                f"[CRASH] delete_project abort: critical cleanup fallito per {project_id}. "
                f"critical={critical_failures} derived={derived_failures}. "
                f"Config row preservata, ritenta o escalation richiesta.",
                op="delete_project",
            )
            from app.shared.exceptions import ProjectCleanupPartialError
            raise ProjectCleanupPartialError(
                project_id=project_id,
                critical_failed=critical_failures,
                derived_failed=derived_failures,
            )

        db.delete_project(project_id, group_id=group_id)
        logger.info(f"Progetto eliminato: {project_id}", op="delete_project")
        return True

    def project_exists(self, project_id: str) -> bool:
        """Verifica se il progetto esiste."""
        return db.get_project(project_id) is not None

    def get_project_columns_metadata(self, project_id: str) -> list:
        """Recupera i metadati delle colonne dal canvas del progetto (DB).

        Sono salvati come `canvas["columns_metadata"]` da update_project_columns_metadata.
        """
        row = db.get_project(project_id)
        if not row:
            return []
        canvas = row.get("canvas", {})
        if isinstance(canvas, str):
            import json
            try:
                canvas = json.loads(canvas)
            except json.JSONDecodeError:
                return []
        return canvas.get("columns_metadata", []) if isinstance(canvas, dict) else []

    def update_project_columns_metadata(self, project_id: str, columns_metadata: list) -> bool:
        """Salva i metadati delle colonne nel canvas del progetto (DB).

        Operazione atomica: usa `update_project_canvas_key_atomic` (SELECT
        FOR UPDATE + merge + UPDATE in transazione) per evitare lost
        update quando executor ESTRAI e altri writer (es. utente che
        modifica canvas da UI, altri worker Celery paralleli) concorrono
        sullo stesso progetto.

        Ritorna True se il progetto esiste ed e' stato aggiornato, False
        se il progetto non esiste.

        **NON** cattura eccezioni DB/transient: queste propagano al caller.
        Motivazione: il helper `app.shared.columns_metadata
        .save_columns_metadata_with_retry` fa retry selettivo sulle
        eccezioni transient (OperationalError, DeadlockDetected, ecc.) e,
        dopo i retry, solleva `ColumnsMetadataSaveError` esplicito per far
        fallire il task Celery invece di ritornare SUCCESS con metadata
        stale. Un wrapper try/except qui dentro avrebbe trasformato un
        transient in un `return False` silenzioso, disabilitando il retry
        e re-introducendo il silent-fallback contro cui il fix nasce.

        Tutti i call-site (estrai/manipola/arricchisci/enrichment_task)
        passano dall'helper condiviso — niente try/except locali.
        """
        import app.db.config_db_service as _cfg_db
        return _cfg_db.update_project_canvas_key_atomic(
            project_id, "columns_metadata", columns_metadata
        )

    # =========================================================================
    # RISULTATI (file output pipeline)
    # =========================================================================

    def save_result(self, project_id: str, filename: str, fmt: str,
                    data: bytes, rows_count: int = None,
                    group_id: str = None) -> str:
        """Salva file risultato come blob nel DB. Ritorna result_id."""
        result_id = str(uuid.uuid4())
        group_id = group_id or self.DEFAULT_GROUP
        db.save_result(
            result_id=result_id,
            project_id=project_id,
            group_id=group_id,
            filename=filename,
            fmt=fmt,
            data=data,
            rows_count=rows_count,
        )
        logger.info(f"Risultato salvato: {result_id} ({len(data)} bytes)", op="save_result")
        return result_id

    def get_latest_result(self, project_id: str) -> Optional[dict]:
        """Recupera il risultato più recente (senza blob)."""
        return db.get_latest_result(project_id)

    def get_result_data(self, result_id: str) -> Optional[bytes]:
        """Recupera il blob del risultato per download."""
        return db.get_result_data(result_id)

    def list_results(self, project_id: str) -> List[dict]:
        """Lista risultati del progetto (senza blob)."""
        return db.list_results(project_id)
