"""
Servizio CRUD per progetti PLENORA-ETL.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import json

import uuid
from app.core.projects.canvas_revision import canvas_version
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Optional
from typing_extensions import TypedDict

from app.utils.logger import get_logger
from app.config import settings
from app.core.projects.models import (
    ProjectMetadata,
    ProjectCreate,
    ProjectList,
    CanvasVersionRef,
)
from app.core.projects.canvas_models import (
    CanvasState,
)

from app.core.auth import db as _groups
from app.core.projects import db as project_db
from app.core.results import store as results_store
from app.core.projects.domain import build_project_id, resolve_project_group_id
from app.core.projects.lineage import detect_project_lineage_cycle
from app.core.projects.exceptions import (
    ProjectLineageCycleError,
)
from app.core.projects.canvas_service import (
    ProjectCanvasService,
    RawProjectCanvas as _RawProjectCanvas,
)
from app.core.projects.lifecycle_service import (
    ProjectDependencyPayload as _ProjectDependencyPayload,
    ProjectLifecycleService,
)
from app.core.projects.metadata_service import ProjectMetadataService
from app.core.projects.output_contract_service import ProjectOutputContractService
from app.core.projects.repository import (
    DbProjectCanvasKeyRepository,
    ProjectCanvasKeyRepository,
)

logger = get_logger(__name__)

ResultMetadataPublicPayload = Mapping[str, object]


class ProjectPublicPayload(TypedDict):
    metadata: Mapping[str, object]
    canvas: object
    id: str


class ProjectService:
    """
    Servizio CRUD per progetti PLENORA-ETL.

    Usa PostgreSQL come storage tramite le leaf DB dedicate.
    group_id viene passato esplicitamente — in futuro verrà dal JWT.
    """

    DEFAULT_GROUP = settings.DEFAULT_GROUP  # configurabile in .env

    def __init__(
        self,
        project_repository: Optional[ProjectCanvasKeyRepository] = None,
    ) -> None:
        self._project_repository = (
            project_repository or DbProjectCanvasKeyRepository()
        )
        # Sub-service a interfaccia stretta: i nuovi chiamanti dipendono da
        # questi, mentre la facade mantiene le API pubbliche esistenti.
        self.metadata = ProjectMetadataService(self)
        self.canvas = ProjectCanvasService(self)
        self.output_contract = ProjectOutputContractService()
        self.lifecycle = ProjectLifecycleService(self)

    def _generate_project_id(self, name: str) -> str:
        """Genera ID univoco leggibile: {nome_safe}_{timestamp}_{uuid8}."""
        return build_project_id(name, datetime.now(timezone.utc), str(uuid.uuid4()))

    def _ensure_default_group(self):
        """Crea il gruppo default se non esiste."""
        if not _groups.get_group(self.DEFAULT_GROUP):
            _groups.create_group(
                self.DEFAULT_GROUP, "Default", "Gruppo di default"
            )

    @staticmethod
    def project_row_to_metadata(
        row: project_db.ProjectRow,
    ) -> ProjectMetadata:
        """Serialize a typed ProjectRow into the public metadata model.

        La description arriva da `row.description`; stringhe vuote o valori
        null vengono normalizzati a `""` nel modello pubblico.
        """
        return ProjectMetadata(
            id=row.id,
            name=row.name,
            description=row.description or "",
            project_type=row.project_type,
            default_connection_id=row.connection_id,
            group_id=resolve_project_group_id(
                row.group_id, settings.DEFAULT_GROUP
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
            canvas_validated=row.canvas_validated or False,
            tags=list(getattr(row, "tags", None) or []),
        )

    @staticmethod
    def project_detail_row_to_metadata(
        row: project_db.ProjectDetailRow,
    ) -> ProjectMetadata:
        """Serialize a typed ProjectDetailRow into the public metadata model."""
        return ProjectMetadata(
            id=row.id,
            name=row.name,
            description=row.description or "",
            project_type=row.project_type,
            default_connection_id=row.connection_id,
            group_id=resolve_project_group_id(
                row.group_id, settings.DEFAULT_GROUP
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
            canvas_validated=row.canvas_validated or False,
            tags=list(getattr(row, "tags", None) or []),
        )

    def create_project(
        self,
        project_data: ProjectCreate,
        group_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> ProjectMetadata:
        """Crea nuovo progetto nel DB."""
        self._ensure_default_group()
        group_id = resolve_project_group_id(group_id, self.DEFAULT_GROUP)
        project_id = self._generate_project_id(project_data.name)

        row = project_db.create_project_row(
            project_id=project_id,
            group_id=group_id,
            name=project_data.name,
            project_type=project_data.project_type,
            connection_id=project_data.default_connection_id,
            created_by=created_by,
        )
        logger.info(f"Progetto creato: {project_id} (created_by={created_by})", op="create_project")
        return self.project_detail_row_to_metadata(row)

    def clone_project(
        self,
        source_project_id: str,
        new_name: str,
        group_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Optional[ProjectMetadata]:
        """Clone progetto delegando al sub-service lifecycle."""
        return self.lifecycle.clone_project(
            source_project_id,
            new_name,
            group_id=group_id,
            created_by=created_by,
        )

    def update_project_details(
        self,
        project_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list] = None,
    ) -> Optional[ProjectMetadata]:
        """Aggiorna nome, descrizione e/o tag del progetto.

        Tocca solo i metadati: NON modifica il canvas né lo stato di
        validazione. Ritorna None se il progetto non esiste; i metadati
        aggiornati altrimenti.
        """
        fields: dict[str, object] = {}
        if name is not None:
            fields["name"] = name
        if description is not None:
            fields["description"] = description
        if tags is not None:
            # Normalizza: stringhe non vuote, trimmed, deduplicate, max 20.
            seen: set[str] = set()
            clean: list[str] = []
            for t in tags:
                s = str(t).strip()
                if s and s.lower() not in seen:
                    seen.add(s.lower())
                    clean.append(s)
            fields["tags"] = clean[:20]
        if not fields:
            return self.get_project_metadata(project_id)
        row = project_db.update_project_row(project_id, **fields)
        return self.project_detail_row_to_metadata(row) if row else None

    # ── Versioning canvas (#12) ────────────────────────────────────────────

    def create_canvas_version(
        self,
        project_id: str,
        label: Optional[str] = None,
        group_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Optional[CanvasVersionRef]:
        """Crea una versione del canvas."""
        return self.canvas.create_canvas_version(
            project_id, label=label, group_id=group_id, created_by=created_by,
        )

    def list_canvas_versions(self, project_id: str) -> list[CanvasVersionRef]:
        """Lista le versioni del canvas."""
        return self.canvas.list_canvas_versions(project_id)

    def restore_canvas_version(
        self, project_id: str, version_id: str, *, base_canvas_version: str | None = None, revision_out: dict | None = None
    ) -> Optional[CanvasState]:
        """Ripristina una versione del canvas."""
        return self.canvas.restore_canvas_version(project_id, version_id, **({"base_canvas_version": base_canvas_version} if base_canvas_version is not None else {}), **({"revision_out": revision_out} if revision_out is not None else {}))

    def rollback_created_project(
        self,
        project_id: str,
        group_id: Optional[str] = None,
    ) -> bool:
        """Delete a project row created in a failed create request.

        This compensation is intentionally narrower than ``delete_project``:
        during create rollback the project has not been exposed as a durable
        user resource yet, so only the config row created in the same request
        should be removed.
        """
        return project_db.delete_project(project_id, group_id=group_id)

    def list_projects(
        self,
        group_id: Optional[str] = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> ProjectList:
        """Lista progetti delegando al sub-service metadata."""
        return self.metadata.list_projects(group_id, limit=limit, offset=offset)

    def get_project_metadata(self, project_id: str, group_id: Optional[str] = None) -> Optional[ProjectMetadata]:
        """Carica metadati progetto delegando al sub-service metadata."""
        return self.metadata.get_project_metadata(project_id, group_id=group_id)

    def get_project_canvas(self, project_id: str) -> Optional[CanvasState]:
        """Carica il canvas del progetto."""
        return self.canvas.get_project_canvas(project_id)

    def get_canvas_version(self, project_id: str) -> Optional[str]:
        """Token della versione del canvas persistito (None se il progetto non esiste)."""
        raw = self.canvas.get_project_raw_canvas(project_id)
        return canvas_version(raw.canvas) if raw is not None else None

    def get_project_raw_canvas(
        self,
        project_id: str,
        group_id: Optional[str] = None,
    ) -> Optional[_RawProjectCanvas]:
        """Carica il canvas raw del progetto."""
        return self.canvas.get_project_raw_canvas(project_id, group_id=group_id)

    def get_project(
        self,
        project_id: str,
        group_id: Optional[str] = None,
    ) -> Optional[ProjectPublicPayload]:
        """Carica progetto completo (metadata + canvas).

        - group_id=None → no filter (qualsiasi gruppo); il check di accesso
          spetta al chiamante.
        - group_id="X" → solo se appartiene al gruppo X.
        """
        row = project_db.get_project_row(project_id, group_id=group_id)
        if not row:
            return None
        metadata = self.project_detail_row_to_metadata(row)
        canvas = row.canvas or {"nodes": [], "edges": []}
        return {
            "metadata": metadata.model_dump() if metadata else {},
            "canvas": canvas,
            "id": project_id,
        }

    def update_canvas(self, project_id: str, canvas: CanvasState, *, base_canvas_version: str | None = None, revision_out: dict | None = None) -> bool:
        """Aggiorna il canvas mantenendo la semantica della facade:
        lineage guard, reset canvas_validated, cascade ai progetti a valle.

        Raises:
            ProjectLineageCycleError: se i project_source nel canvas +
                le dipendenze transitive degli altri progetti formano un
                ciclo. Il router traduce in HTTP 400.
        """
        return self.canvas.update_canvas(project_id, canvas, **({"base_canvas_version": base_canvas_version} if base_canvas_version is not None else {}), **({"revision_out": revision_out} if revision_out is not None else {}))

    def set_canvas_validated(self, project_id: str, value: bool) -> bool:
        """Persisti lo stato di validazione canvas."""
        return self.canvas.set_canvas_validated(project_id, value)

    def get_project_output_contract(
        self,
        project_id: str,
    ) -> Mapping[str, object] | None:
        """Carica l'output contract del progetto."""
        return self.output_contract.get_project_output_contract(project_id)

    def update_project_output_contract(
        self,
        project_id: str,
        output_contract: Mapping[str, object],
    ) -> bool:
        """Aggiorna l'output contract del progetto."""
        return self.output_contract.update_project_output_contract(
            project_id,
            output_contract,
        )

    def _assert_no_lineage_cycle(self, project_id: str, canvas: CanvasState) -> None:
        """Verifica che salvare `canvas` per `project_id` non introduca
        un ciclo nel DAG delle dipendenze cross-project.

        Costruisce una `get_sources_fn` che per il progetto corrente usa
        il CANVAS NUOVO (non ancora salvato) e per tutti gli altri legge
        il canvas dal DB. Cosi' il check anticipa la validazione al save
        invece di scoprire il ciclo solo al prossimo run.
        """
        new_sources = [
            n.source_project_id
            for n in canvas.nodes
            if n.type == "project_source" and n.source_project_id
        ]

        def get_sources(pid: str):
            if pid == project_id:
                return new_sources
            row = project_db.get_project_row(pid)
            if not row:
                return []
            canvas_dict = row.canvas or {}
            if isinstance(canvas_dict, str):
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
            raise ProjectLineageCycleError(chain)

    def _find_dependent_projects(
        self,
        project_id: str,
        group_id: Optional[str] = None,
    ) -> list[_ProjectDependencyPayload]:
        """Trova progetti dipendenti tramite il sub-service lifecycle.

        Resta sulla facade come hook: i chiamanti interni (update_canvas
        cascade, delete_project) passano da qui, quindi i patch di classe
        continuano a intercettare.
        """
        return self.lifecycle.find_dependent_projects(project_id, group_id=group_id)

    def delete_project(self, project_id: str, group_id: Optional[str] = None) -> bool:
        """Cancella un progetto tramite il sub-service lifecycle.

        Semantica invariata:
        blocco dipendenze (eseguibili + progetti downstream), cascade con
        critical/derived failures, fail-closed sulla row config.

        Raises:
            ProjectHasDependenciesError: eseguibili o progetti downstream
                collegati — vanno eliminati prima.
            ProjectCleanupPartialError: step critical falliti, row preservata.
        """
        return self.lifecycle.delete_project(project_id, group_id=group_id)

    def project_exists(self, project_id: str) -> bool:
        """Verifica se il progetto esiste (delega al sub-service metadata)."""
        return self.metadata.project_exists(project_id)

    def save_result(
        self,
        project_id: str,
        filename: str,
        fmt: str,
        data: bytes,
        rows_count: Optional[int] = None,
        group_id: Optional[str] = None,
    ) -> str:
        """Salva il risultato su object storage. Ritorna result_id."""
        group_id = resolve_project_group_id(group_id, self.DEFAULT_GROUP)
        from app.core.results.blob_writer import save_result_blob_from_bytes

        result_id = save_result_blob_from_bytes(
            project_id=project_id,
            group_id=group_id,
            filename=filename,
            fmt=fmt,
            data=data,
            rows_count=rows_count,
        )
        logger.info(f"Risultato salvato: {result_id} ({len(data)} bytes)", op="save_result")
        return result_id

    def get_latest_result(
        self,
        project_id: str,
    ) -> Optional[ResultMetadataPublicPayload]:
        """Recupera il risultato più recente (senza blob)."""
        return results_store.get_latest_result(project_id)

    def get_result_data(self, result_id: str) -> Optional[bytes]:
        """Recupera il blob del risultato per download."""
        return results_store.get_result_data(result_id)

    def list_results(self, project_id: str) -> list[ResultMetadataPublicPayload]:
        """Lista risultati del progetto (senza blob)."""
        return results_store.list_results(project_id)
