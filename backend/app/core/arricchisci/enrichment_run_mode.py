"""EnrichmentRunMode — strategy che incapsula le divergenze standard vs
eseguibile di ``execute_enrichment_task`` (#28-B).

Increment 1 (questo file): concern di SETUP — auth recheck, logger project_id,
risoluzione group_id + creazione intermediate run. Gli altri concern (persist
output S3-vs-DB, shape risposta, finalize run-logging) restano per ora inline
nel task, gated da ``is_eseguibile``, e migreranno qui negli increment 2/3.

Gli import usano gli STESSI alias-modulo del task così che i monkeypatch dei
test (che patchano gli attributi sui moduli sorgente) restino efficaci.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from app.core.eseguibili import db as eseguibili_db
from app.core.intermediate import run_lifecycle as run_lifecycle_module
from app.core.projects import retention as project_retention
from app.core.projects import service as project_service_module
from app.utils.logger import get_logger

logger = get_logger("app.core.arricchisci.enrichment_run_mode")


@dataclass
class RunContext:
    """Esito del setup: run_id (None per eseguibile), group_id, retention_n."""

    run_id: Optional[str]
    group_id: str
    retention_n: Optional[int]


class EnrichmentRunMode:
    """Base: standard (progetto) vs eseguibile (pipeline pubblicata on-demand)."""

    is_eseguibile: bool = False

    def should_recheck_auth(self) -> bool:
        raise NotImplementedError

    def log_project_id(self, project_id: str) -> str:
        raise NotImplementedError

    def begin_run(self, *, project_id: str, user_id: Optional[str], task_id: str) -> RunContext:
        raise NotImplementedError


class StandardRunMode(EnrichmentRunMode):
    is_eseguibile = False

    def should_recheck_auth(self) -> bool:
        return True

    def log_project_id(self, project_id: str) -> str:
        return project_id

    def begin_run(self, *, project_id: str, user_id: Optional[str], task_id: str) -> RunContext:
        run_id: Optional[str] = str(uuid.uuid4())
        group_id = "unknown"
        retention_n: Optional[int] = None
        try:
            _meta = project_service_module.ProjectService().get_project_metadata(project_id)
            group_id = getattr(_meta, "group_id", "unknown") if _meta else "unknown"
            retention_n = project_retention.resolve_runs_retention_n(
                project_id,
                default=run_lifecycle_module.get_default_runs_retention(),
            )
            run_lifecycle_module.create_run(
                run_id=run_id,
                canvas_id=project_id,
                group_id=group_id,
                user_id=user_id,
            )
        except Exception as e:
            logger.warning(
                f"[Task {task_id}] Intermediate: apertura run fallita (non bloccante): {e}"
            )
            run_id = None
        return RunContext(run_id=run_id, group_id=group_id, retention_n=retention_n)


class EseguibileRunMode(EnrichmentRunMode):
    is_eseguibile = True

    def should_recheck_auth(self) -> bool:
        # Gli eseguibili hanno flusso di auth separato (token eseguibile).
        return False

    def log_project_id(self, project_id: str) -> str:
        # Niente project_id ai logger: eviterebbe tracce persistenti su logs.project_id.
        return ""

    def begin_run(self, *, project_id: str, user_id: Optional[str], task_id: str) -> RunContext:
        # No intermediate run (l'eseguibile non e' un progetto in `projects`);
        # group_id dalla tabella `eseguibili` per il tenant binding a valle.
        group_id = ""
        try:
            _eseg = eseguibili_db.get_eseguibile(project_id)
            group_id = (_eseg.get("group_id") or "") if _eseg else ""
        except Exception as e:
            logger.warning(
                f"[Task {task_id}] Eseguibile group_id lookup fallito (non bloccante): {e}"
            )
        return RunContext(run_id=None, group_id=group_id, retention_n=None)


def for_task(is_eseguibile: bool) -> EnrichmentRunMode:
    """Factory: la run-mode appropriata per il task."""
    return EseguibileRunMode() if is_eseguibile else StandardRunMode()


__all__ = [
    "RunContext",
    "EnrichmentRunMode",
    "StandardRunMode",
    "EseguibileRunMode",
    "for_task",
]
