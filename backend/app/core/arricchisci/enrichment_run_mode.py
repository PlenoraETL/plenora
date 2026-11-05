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
from app.core.eseguibili import service as eseguibili_service
from app.core.intermediate import run_lifecycle as run_lifecycle_module
from app.core.eseguibili.output_persistence import ExecutableOutputPersistence
from app.core.projects import retention as project_retention
from app.core.projects import service as project_service_module
from app.core.results import pipeline_result as pipeline_result_module
from app.utils.logger import get_logger
from app.shared.contracts.payloads import JsonRows
from app.shared.execution.runtime_identity import task_result, trigger_for_test_run

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
    # Output nello spazio dell'attivita' (object storage per task_id), mai
    # nei risultati del progetto ne' nel suo output_contract: Eseguibili ed
    # esecuzioni di prova (runtime_identity, regola H).
    isolated_output: bool = False

    # Predicati di policy: incapsulano le branch "standard sì / isolato no"
    # del task senza spostarne il meccanismo.
    def logs_run(self) -> bool:
        """True se la run va tracciata su log_runs (cronologia)."""
        return not self.is_eseguibile

    def triggers_downstream_carica(self) -> bool:
        """True se a fine run (output file) vanno lanciati i nodi CARICA collegati."""
        return not self.isolated_output

    def triggers_carica_after_dataframe(self) -> bool:
        """Come sopra per l'output tabellare. Eseguibile: invariato rispetto
        a prima (il lancio non era condizionato); la prova non lancia mai."""
        return True

    def needs_full_bytes(self) -> bool:
        """True se serve sempre il fetch dei bytes completi (output isolato→upload S3)."""
        return self.isolated_output

    def triggered_by(self) -> str:
        """``log_runs.triggered_by`` della run."""
        return "manual"

    def should_recheck_auth(self) -> bool:
        raise NotImplementedError

    def log_project_id(self, project_id: str) -> str:
        raise NotImplementedError

    def begin_run(
        self,
        *,
        project_id: str,
        user_id: Optional[str],
        task_id: str,
        run_id: Optional[str] = None,
        canonical_group_id: Optional[str] = None,
    ) -> RunContext:
        raise NotImplementedError

    def persist_dataframe_result(
        self, *, project_id: str, task_id: str, final_df: Any,
        final_format: str, output_filename: str, final_filename: str,
        group_id: str,
        source_names: Optional[Mapping[str, str]] = None, column_plan: Optional[JsonRows] = None,
    ) -> Tuple[Optional[str], Path]:
        """Persiste il DataFrame finale. Ritorna (result_id|None, saved_path).

        ``column_plan`` trasporta gli id preparati sul canvas eseguito.
        ``source_names`` resta accettato per compatibilità della firma;
        non viene usato per ricostruire identità dopo l'esecuzione.
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

    def begin_run(
        self,
        *,
        project_id: str,
        user_id: Optional[str],
        task_id: str,
        run_id: Optional[str] = None,
        canonical_group_id: Optional[str] = None,
    ) -> RunContext:
        # Il broker assegna un run_id stabile (normalmente il message_id).
        # Preservarlo e' essenziale: la consegna e' at-least-once e il guard
        # idempotente del task cerca proprio questa run al tentativo seguente.
        effective_run_id = run_id or str(uuid.uuid4())
        _meta = project_service_module.ProjectService().get_project_metadata(project_id)
        if _meta is None:
            raise LookupError(f"Progetto non trovato durante setup run: {project_id}")
        group_id = str(getattr(_meta, "group_id", "") or "")
        if not group_id:
            raise RuntimeError("group_id mancante durante setup run enrichment")
        retention_n = project_retention.resolve_runs_retention_n(
            project_id,
            default=run_lifecycle_module.get_default_runs_retention(),
        )
        # Fail-closed: senza questa riga non esiste alcuna barriera idempotente
        # per una consegna at-least-once dello stesso messaggio.
        run_lifecycle_module.create_run(
            run_id=effective_run_id,
            canvas_id=project_id,
            group_id=group_id,
            user_id=user_id,
        )
        return RunContext(
            run_id=effective_run_id,
            group_id=group_id,
            retention_n=retention_n,
        )

    def persist_dataframe_result(
        self, *, project_id, task_id, final_df, final_format, output_filename, final_filename,
        group_id, source_names=None, column_plan: Optional[JsonRows] = None,
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
            project_service=_project_service, project_id=project_id,
            result_columns=output_contract_module.result_columns_from_plan(
                project_id=project_id, columns=final_df.columns, dataframe=final_df,
                plan=column_plan or [],
                # Come EnrichExecutor: pubblicazione basata sul piano, senza
                # riconoscimento del contenuto disponibile solo al worker.
            ),
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
    isolated_output = True

    def should_recheck_auth(self) -> bool:
        # Gli eseguibili hanno flusso di auth separato (token eseguibile).
        return False

    def log_project_id(self, project_id: str) -> str:
        # Niente project_id ai logger: eviterebbe tracce persistenti su logs.project_id.
        return ""

    def begin_run(
        self,
        *,
        project_id: str,
        user_id: Optional[str],
        task_id: str,
        run_id: Optional[str] = None,
        canonical_group_id: Optional[str] = None,
    ) -> RunContext:
        # Il contratto accodato e' il tenant binding canonico. Il lookup resta
        # soltanto per i task legacy gia' presenti nel broker al deploy.
        group_id = str(canonical_group_id or "").strip()
        if not group_id:
            group_id = str(
                eseguibili_service.get_eseguibile_group_id(project_id) or ""
            ).strip()
        if not group_id:
            raise RuntimeError(
                "group_id eseguibile non determinabile durante setup enrichment"
            )
        return RunContext(run_id=None, group_id=group_id, retention_n=None)

    def persist_dataframe_result(
        self, *, project_id, task_id, final_df, final_format, output_filename, final_filename,
        group_id, source_names=None, column_plan: Optional[JsonRows] = None,
    ):
        # Eseguibile: output DataFrame → object storage (S3) via streaming chunks.
        # Niente DB result/output_contract. result_id resta None (il frontend
        # scarica via download_url, vedi decorate_result).
        saved = ExecutableOutputPersistence(
            task_id=task_id,
            group_id=group_id,
        ).persist_dataframe_output(
            df=final_df,
            output_format=final_format,
            filename_stem=Path(final_filename).stem,
        )
        logger.info(
            f"[Task {task_id}] Output eseguibile (df) su storage: "
            f"{saved.storage_key}"
        )
        return None, Path(saved.output_filename)

    def decorate_result(self, result, *, project_id, task_id):
        result["download_url"] = f"/api/eseguibili/{project_id}/download/{task_id}"
        result["is_eseguibile"] = True


class ProjectTestRunMode(EnrichmentRunMode):
    """«Esegui di prova» dall'editor (runtime_identity, regola H): permessi e
    cronologia del progetto (riga marcata ``test_run:<username>``), output
    isolato come un Eseguibile, nessuna run intermedia, nessun Carica,
    nessun output_contract."""

    is_eseguibile = False
    isolated_output = True

    def __init__(self, username: str) -> None:
        self.username = username

    def should_recheck_auth(self) -> bool:
        return True

    def log_project_id(self, project_id: str) -> str:
        return project_id

    def triggers_carica_after_dataframe(self) -> bool:
        return False

    def triggered_by(self) -> str:
        return trigger_for_test_run(self.username)

    def begin_run(
        self,
        *,
        project_id: str,
        user_id: Optional[str],
        task_id: str,
        run_id: Optional[str] = None,
        canonical_group_id: Optional[str] = None,
    ) -> RunContext:
        # Nessuna run intermedia (cronologia dei nodi e retention del
        # progetto invariate): il tenant e' quello del contratto.
        group_id = str(canonical_group_id or "").strip()
        if not group_id:
            raise RuntimeError("group_id mancante durante setup dell'esecuzione di prova")
        return RunContext(run_id=None, group_id=group_id, retention_n=None)

    def persist_dataframe_result(
        self, *, project_id: str, task_id: str, final_df: Any, final_format: str, output_filename: str,
        final_filename: str, group_id: str, source_names: Optional[Mapping[str, str]] = None,
        column_plan: Optional[JsonRows] = None,
    ) -> Tuple[Optional[str], Path]:
        saved = ExecutableOutputPersistence(
            task_id=task_id,
            group_id=group_id,
        ).persist_dataframe_output(
            df=final_df,
            output_format=final_format,
            filename_stem=Path(final_filename).stem,
        )
        logger.info(f"[Task {task_id}] Output dell'esecuzione di prova su storage: {saved.storage_key}")
        return None, Path(saved.output_filename)

    def decorate_result(self, result: MutableMapping[str, Any], *, project_id: str, task_id: str) -> None:
        # Download da /api/tasks/{task_id}/download (proprietario del task);
        # il segno della prova dall'unica funzione dei risultati.
        result.update(task_result({"result_id": None}, {"username": self.username}))


def for_task(is_eseguibile: bool, test_run: Optional[Mapping[str, Any]] = None) -> EnrichmentRunMode:
    """Factory: la run-mode appropriata per il task."""
    if is_eseguibile:
        return EseguibileRunMode()
    if test_run is not None:
        return ProjectTestRunMode(str(test_run.get("username") or ""))
    return StandardRunMode()


__all__ = [
    "RunContext",
    "EnrichmentRunMode",
    "StandardRunMode",
    "EseguibileRunMode",
    "ProjectTestRunMode",
    "for_task",
]
