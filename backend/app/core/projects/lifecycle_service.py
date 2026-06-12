"""Sub-service lifecycle/dependency progetto (Epic F roadmap, fetta F5).

Clone, cancellazione con cascade fail-closed e dependency scan vivono qui
come interfaccia stretta estratta dalla facade ``ProjectService``.

Pattern owner-backref (come F2/F3): i cross-call passano dalla facade
(``get_project_metadata``, ``create_project``, ``update_canvas``,
``_find_dependent_projects``...) cosi' i patch di classe storici sui
metodi della facade restano efficaci attraverso la delega.
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING, Optional

from typing_extensions import TypedDict

from app.core.eseguibili.db import list_eseguibili_by_source
from app.core.intermediate import cleanup as intermediate_cleanup_module
from app.core.projects import cleanup as project_cleanup_module
from app.core.projects import db as project_db
from app.core.projects.exceptions import (
    ProjectCleanupPartialError,
    ProjectHasDependenciesError,
)
from app.core.projects.models import ProjectCreate, ProjectMetadata
from app.core.results import cleanup as results_cleanup_module
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.core.projects.service import ProjectService

logger = get_logger(__name__)


class ProjectDependencyPayload(TypedDict):
    id: str
    name: str
    project_type: str


class ProjectLifecycleService:
    """Clone, delete con cascade e dependency scan del progetto."""

    def __init__(self, owner: "ProjectService") -> None:
        self._owner = owner

    def clone_project(
        self,
        source_project_id: str,
        new_name: str,
        group_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Optional[ProjectMetadata]:
        """Duplica un progetto in uno nuovo (id nuovo).

        Copia metadata (type, connessione, descrizione) + canvas as-is +
        output_contract (con ``producer_project_id`` riscritto al nuovo id e
        ``canvas_validated`` resettato dal salvataggio canvas). Ritorna None se
        la sorgente non esiste.

        Il canvas è copiato AS-IS: i node id interni restano coerenti con gli
        edge; i nodi ``project_source`` restano legati alle stesse sorgenti (il
        clone legge dagli stessi progetti). Nessun ciclo introdotto: il clone
        non è referenziato da nessuno.
        """
        owner = self._owner
        source = owner.get_project_metadata(source_project_id)
        if source is None:
            return None

        cloned = owner.create_project(
            ProjectCreate(
                name=new_name,
                description=source.description,
                project_type=source.project_type,
                default_connection_id=source.default_connection_id,
            ),
            group_id=group_id,
            created_by=created_by,
        )

        source_canvas = owner.get_project_canvas(source_project_id)
        if source_canvas is not None and (source_canvas.nodes or source_canvas.edges):
            owner.update_canvas(cloned.id, source_canvas)

        source_contract = owner.get_project_output_contract(source_project_id)
        if source_contract:
            cloned_contract = dict(source_contract)
            cloned_contract["producer_project_id"] = cloned.id
            owner.update_project_output_contract(cloned.id, cloned_contract)

        logger.info(
            f"Progetto clonato: {source_project_id} -> {cloned.id} (created_by={created_by})",
            op="clone_project",
        )
        return cloned

    def find_dependent_projects(
        self,
        project_id: str,
        group_id: Optional[str] = None,
    ) -> list[ProjectDependencyPayload]:
        """Trova progetti manipola/arricchisci che usano `project_id` come sorgente
        in un nodo `project_source` del proprio canvas.

        Cerca solo nello stesso gruppo (un progetto non può referenziare risorse
        di altri gruppi). Ritorna [{id, name, project_type}, ...].
        """
        # Recupera la riga del progetto per leggerne il group_id se non passato
        if group_id is None:
            row = project_db.get_project_row(project_id)
            if not row:
                return []
            group_id = row.group_id

        rows = (
            row
            for row in project_db.list_project_canvas_check_rows()
            if row.group_id == group_id
            and row.project_type in ("manipola", "arricchisci")
        )

        dependents: list[ProjectDependencyPayload] = []
        for cand in rows:
            cand_id = cand.id
            if not cand_id or cand_id == project_id:
                continue
            canvas = cand.canvas or {}
            if isinstance(canvas, str):
                # intent: optional feature — canvas corrotto del candidato non blocca la
                # scansione. Narrow difensivo come in _assert_no_lineage_cycle.get_sources
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
                        "name": cand.name or cand_id,
                        "project_type": cand.project_type or "",
                    })
                    break
        return dependents

    def delete_project(
        self, project_id: str, group_id: Optional[str] = None
    ) -> bool:
        """Elimina progetto e tutti i risultati collegati.

        - group_id=None → no filter (chiamante è superadmin o ha già fatto il check)
        - group_id="X" → solo se appartiene al gruppo X

        Solleva ProjectHasDependenciesError se esistono eseguibili pubblicati
        a partire da questo progetto: vanno eliminati prima.
        """
        row = project_db.get_project_row(project_id, group_id=group_id)
        if not row:
            return False

        # Blocco: non eliminare progetti con eseguibili collegati o progetti
        # downstream che lo usano come sorgente. Si raccolgono entrambi e si
        # solleva un'unica eccezione per dare all'utente il quadro completo
        linked_eseguibili = list_eseguibili_by_source(project_id)
        dependent_projects = self._owner._find_dependent_projects(
            project_id, group_id=row.group_id
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
        # fallimento e' tollerato (metadata/config derivata, recuperabile
        # Se `critical_failures` e' non vuoto al termine, la row `config.projects`
        # NON viene rimossa (fail-closed) e si solleva RuntimeError con lista
        # step falliti: il caller deve ritentare o escalare
        critical_failures: list[str] = []
        derived_failures: list[str] = []

        # intent: cascade step derived — mark_as_orphan e' idempotente, un
        # fallimento e' recuperabile via UI scheduler
        # outcome: fail-open con breadcrumb (exc_info per diagnosi
        # Non cancelliamo gli schedule: lo storico (log esecuzioni) resta
        # visibile e l'utente decide se rimuoverli manualmente dalla UI
        try:
            from app.core.scheduler.schedule_service import ScheduleService
            schedule_service = ScheduleService()
            # Tenant fix: senza `group_id` esplicito,
            # `get_schedules_for_project` (-> `list_schedules`) ricade
            # su `DEFAULT_GROUP='default'`, quindi gli schedule di
            # progetti di altri gruppi non vengono mai marcati orphan
            # e restano `idle` referenziando un project_id ormai
            # cancellato. Passiamo `row.group_id` per cercare nel
            # tenant corretto.
            project_schedules = schedule_service.get_schedules_for_project(
                project_id, group_id=row.group_id,
            )
            # Pre-fix: comparison vs literal "orphan" — typo silente
            # avrebbe ri-marcato anche schedule gia' orfani.
            # Post-fix: enum value comparison (StrEnum -> string canonica).
            from app.core.scheduler.runtime_status import RuntimeStatus
            orphaned_count = 0
            for sched in project_schedules:
                if sched.runtime.status != RuntimeStatus.ORPHAN:
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
        # dinamiche contengono dati utente (volume potenzialmente grande
        # outcome: fail-closed via aggregazione (vedi gate sotto
        try:
            deleted_runs = intermediate_cleanup_module.delete_runs_for_canvas(
                project_id
            )
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
        # utente esportati (GDPR-sensitive
        # outcome: fail-closed via aggregazione
        try:
            deleted_results = results_cleanup_module.delete_results_for_project(
                project_id
            )
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

        # intent: cascade step derived — comparison_keys e' config derivata
        # volume minimo e nessun dato utente sensibile. Orfano innocuo
        # outcome: fail-open con breadcrumb
        # Cleanup comparison_keys (config tables) — chiavi di confronto associate
        # ai nodi del canvas, identificate da canvas_id == project_id
        # Boundary: app.core.projects.cleanup
        try:
            project_cleanup_module.delete_comparison_keys_for_project(project_id)
        except Exception as e:
            logger.warning(
                f"Cleanup comparison_keys fallito per {project_id}: {e}",
                op="delete_project",
                exc_info=True,
            )
            derived_failures.append("comparison_keys")

        if critical_failures:
            logger.error(
                f"[CRASH] delete_project abort: critical cleanup fallito per {project_id}. "
                f"critical={critical_failures} derived={derived_failures}. "
                f"Config row preservata, ritenta o escalation richiesta.",
                op="delete_project",
            )
            raise ProjectCleanupPartialError(
                project_id=project_id,
                critical_failed=critical_failures,
                derived_failed=derived_failures,
            )

        project_db.delete_project(project_id, group_id=group_id)
        logger.info(f"Progetto eliminato: {project_id}", op="delete_project")
        return True


__all__ = ["ProjectDependencyPayload", "ProjectLifecycleService"]
