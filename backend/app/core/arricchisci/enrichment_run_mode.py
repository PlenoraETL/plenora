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
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Tuple

from app.core import output_contract as output_contract_module
from app.core.dataframes import export_writer as dataframe_export_writer
from app.core.eseguibili import service as eseguibili_service
from app.core.eseguibili import storage as eseg_storage
from app.core.intermediate import run_lifecycle as run_lifecycle_module
from app.core.projects import retention as project_retention
from app.core.projects import service as project_service_module
from app.core.results import pipeline_result as pipeline_result_module
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

    # Predicati di policy (derivano da is_eseguibile): incapsulano le branch
    # "standard sì / eseguibile no" del task senza spostarne il meccanismo.
    def logs_run(self) -> bool:
        """True se la run va tracciata su DB (intermediate run + log_runs)."""
        return not self.is_eseguibile

    def triggers_downstream_carica(self) -> bool:
        """True se a fine run vanno lanciati i nodi CARICA collegati."""
        return not self.is_eseguibile

    def needs_full_bytes(self) -> bool:
        """True se serve sempre il fetch dei bytes completi (eseguibile→upload S3)."""
        return self.is_eseguibile

    def should_recheck_auth(self) -> bool:
        raise NotImplementedError

    def log_project_id(self, project_id: str) -> str:
        raise NotImplementedError

    def begin_run(self, *, project_id: str, user_id: Optional[str], task_id: str) -> RunContext:
        raise NotImplementedError

    def persist_dataframe_result(
        self, *, project_id: str, task_id: str, final_df: Any,
        final_format: str, output_filename: str, final_filename: str,
        group_id: str,
        source_names: Optional[Mapping[str, str]] = None,
    ) -> Tuple[Optional[str], Path]:
        """Persiste il DataFrame finale. Ritorna (result_id|None, saved_path).

        ``source_names`` mappa physical_name → identita' stabile (es. il
        ``path`` dell'output_mapping API): consente a build_output_contract
        di riusare l'id colonna attraverso un rename del nome fisico.
        """
        raise NotImplementedError

    def decorate_result(self, result: MutableMapping[str, Any], *, project_id: str, task_id: str) -> None:
        """Arricchisce il dict risultato con campi mode-specifici (in-place)."""
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

    def persist_dataframe_result(
        self, *, project_id, task_id, final_df, final_format, output_filename, final_filename,
        group_id, source_names=None,
    ):
        # Arricchisci standard: typed pipeline-result boundary (DB) + output_contract.
        saved = pipeline_result_module.save_pipeline_result_outcome(
            project_id=project_id,
            df=final_df,
            output_format=final_format,
            filename_stem=output_filename,
            rows_count=len(final_df),
        )
        result_id = saved.result_id
        saved_path = Path(saved.output_filename)
        logger.info(f"[Task {task_id}] Risultato persistito: {result_id}")

        _project_service = project_service_module.ProjectService()
        dumped_contract = output_contract_module.persist_output_contract(
            project_service=_project_service,
            project_id=project_id,
            result_columns=[
                {
                    "name": col,
                    "source_name": (source_names or {}).get(col, col),
                    "type": str(final_df[col].dtype),
                }
                for col in final_df.columns
            ],
        )
        logger.info(
            f"[Task {task_id}] output_contract salvato: "
            f"{len(dumped_contract.get('columns', []))} colonne"
        )
        return result_id, saved_path

    def decorate_result(self, result, *, project_id, task_id):
        # Standard: nessun download_url (il frontend usa result_id).
        return


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
            group_id = eseguibili_service.get_eseguibile_group_id(project_id)
        except Exception as e:
            logger.warning(
                f"[Task {task_id}] Eseguibile group_id lookup fallito (non bloccante): {e}"
            )
        return RunContext(run_id=None, group_id=group_id, retention_n=None)

    def persist_dataframe_result(
        self, *, project_id, task_id, final_df, final_format, output_filename, final_filename,
        group_id, source_names=None,
    ):
        # Eseguibile: output DataFrame → object storage (S3) via streaming chunks.
        # Niente DB result/output_contract. result_id resta None (il frontend
        # scarica via download_url, vedi decorate_result).
        fmt_lower = (final_format or "").lower()
        # SHP esce zippato: rinomina il filename su storage in `.zip`.
        storage_filename = (
            Path(final_filename).with_suffix(".zip").name
            if fmt_lower == "shp"
            else final_filename
        )
        eseg_storage.save_chunks(
            task_id,
            storage_filename,
            dataframe_export_writer.dataframe_to_export_chunks(final_df, fmt_lower),
            group_id=group_id,
        )
        logger.info(
            f"[Task {task_id}] Output eseguibile (df) su storage streaming: "
            f"eseguibili-outputs/{task_id}/{storage_filename}"
        )
        return None, Path(storage_filename)

    def decorate_result(self, result, *, project_id, task_id):
        result["download_url"] = f"/api/eseguibili/{project_id}/download/{task_id}"


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
