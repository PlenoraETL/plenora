"""
Servizio CRUD per progetti URBY-ETL.

Architettura
============
Questo servizio gestisce il ciclo di vita dei progetti.
Storage: SQLite tramite app.db.db_service (niente filesystem per i metadati).

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
    Servizio CRUD per progetti URBY-ETL.

    Usa SQLite come storage tramite db_service.
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
        """Carica canvas del progetto. None se non esiste.

        Su canvas corrotto in DB, ritorna un CanvasState vuoto loggando warning,
        invece di sollevare 500. L'utente vede un canvas vuoto e può rimediare
        ricaricandolo dalla UI.
        """
        row = db.get_project(project_id)
        if not row:
            return None
        canvas_data = row.get("canvas", {})
        if isinstance(canvas_data, str):
            import json
            try:
                canvas_data = json.loads(canvas_data)
            except json.JSONDecodeError as e:
                logger.warning(
                    f"Canvas corrotto per progetto {project_id}: {e}. "
                    f"Restituisco canvas vuoto.",
                    op="get_project_canvas"
                )
                canvas_data = {}
        if not isinstance(canvas_data, dict):
            logger.warning(
                f"Canvas con tipo inatteso per {project_id}: {type(canvas_data).__name__}. "
                f"Restituisco canvas vuoto.",
                op="get_project_canvas"
            )
            canvas_data = {}
        try:
            return CanvasState(**canvas_data)
        except (TypeError, ValueError) as e:
            logger.warning(
                f"Canvas con schema non valido per {project_id}: {e}. "
                f"Restituisco canvas vuoto.",
                op="get_project_canvas"
            )
            return CanvasState()

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
        """Salva canvas e aggiorna updated_at. False se il progetto non esiste."""
        row = db.get_project(project_id)
        if not row:
            return False
        db.update_project(project_id, canvas=canvas.model_dump())
        return True

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

        candidates = []
        for ptype in ("manipola", "arricchisci"):
            candidates.extend(db.list_projects(group_id=group_id, project_type=ptype))

        dependents = []
        for cand in candidates:
            cand_id = cand.get("id")
            if not cand_id or cand_id == project_id:
                continue
            full = db.get_project(cand_id)
            if not full:
                continue
            canvas = full.get("canvas") or {}
            nodes = canvas.get("nodes") or []
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

        # Marca come orphan tutti gli schedule collegati al progetto.
        # Non li cancelliamo: lo storico (log esecuzioni) resta visibile e
        # l'utente decide se rimuoverli manualmente dalla UI scheduler.
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
                op="delete_project"
            )

        # Cleanup intermediate.db (run + tabelle dinamiche) prima di rimuovere il progetto
        try:
            from app.core.intermediate.db import delete_all_runs_for_canvas
            deleted_runs = delete_all_runs_for_canvas(project_id)
            if deleted_runs:
                logger.info(
                    f"Cleanup intermediate: {deleted_runs} run eliminate per progetto {project_id}",
                    op="delete_project"
                )
        except Exception as e:
            logger.warning(
                f"Cleanup intermediate fallito per {project_id}: {e}",
                op="delete_project"
            )
        # Cleanup result blobs da results.db
        try:
            from app.db.results_db_service import delete_results_for_project
            deleted_results = delete_results_for_project(project_id)
            if deleted_results:
                logger.info(
                    f"Cleanup results: {deleted_results} blob eliminati per progetto {project_id}",
                    op="delete_project"
                )
        except Exception as e:
            logger.warning(
                f"Cleanup results fallito per {project_id}: {e}",
                op="delete_project"
            )

        # Cleanup comparison_keys (config.db) — chiavi di confronto associate
        # ai nodi del canvas, identificate da canvas_id == project_id.
        try:
            from app.db.config_db_service import delete_comparison_keys
            delete_comparison_keys(project_id)
        except Exception as e:
            logger.warning(
                f"Cleanup comparison_keys fallito per {project_id}: {e}",
                op="delete_project"
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
        """Salva i metadati delle colonne nel canvas del progetto (DB)."""
        try:
            row = db.get_project(project_id)
            if not row:
                return False
            canvas = row.get("canvas", {})
            if isinstance(canvas, str):
                import json
                canvas = json.loads(canvas)
            canvas["columns_metadata"] = columns_metadata
            db.update_project(project_id, canvas=canvas)
            return True
        except Exception as e:
            logger.error(f"[CRASH] Errore salvataggio columns_metadata: {e}", exc_info=True)
            return False

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
