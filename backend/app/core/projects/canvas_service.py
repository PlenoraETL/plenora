"""Sub-service canvas progetto (Epic F roadmap, fetta F3).

Letture/scritture del canvas, versioning e stato di validazione come
interfaccia stretta estratta dalla facade ``ProjectService``.

Pattern owner-backref (come F2): gli hook storici monkeypatchati dai test
sulla classe facade (``_assert_no_lineage_cycle``,
``_find_dependent_projects``, ``get_project_canvas``/``update_canvas``
stessi) restano di proprieta' della facade; questo sub-service li risolve
attraverso ``owner`` a call-time, cosi' i patch di classe e i cross-call
interni mantengono la semantica storica esatta.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from app.core.projects import db as project_db
from app.core.projects.canvas_models import CanvasState
from app.core.projects.exceptions import CanvasCorruptedError
from app.core.projects.models import CanvasVersionRef
from app.core.output_contract import sync_canvas_with_output_contracts
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.core.projects.service import ProjectService

logger = get_logger(__name__)


@dataclass(frozen=True)
class RawProjectCanvas:
    """Raw persisted canvas value for recovery/debug reads.

    `canvas` is intentionally unvalidated: callers use this only after the
    strict CanvasState path rejects persisted data.
    """

    project_id: str
    canvas: object


class ProjectCanvasService:
    """Canvas: load validato, raw recovery, save con lineage guard,
    versioning e stato di validazione."""

    def __init__(self, owner: "ProjectService") -> None:
        self._owner = owner

    def get_project_canvas(self, project_id: str) -> Optional[CanvasState]:
        """Carica canvas del progetto.

        Ritorna:
          - `None` se il progetto non esiste.
          - `CanvasState` vuoto se il canvas e' `None`/`{}` in DB
            (caso legittimo: progetto appena creato, mai salvato).

        Solleva `CanvasCorruptedError` se il canvas e' presente ma
        non interpretabile (JSON malformato, tipo inatteso, schema
        Pydantic non valido). La corruzione e' fail-loud:
        il router traduce in 500 con messaggio esplicito e l'utente
        puo' chiedere il restore da backup.
        """
        row = project_db.get_project_row(project_id)
        if not row:
            return None

        canvas_data = row.canvas

        if canvas_data is None or canvas_data == {} or canvas_data == "":
            return CanvasState()

        if isinstance(canvas_data, str):
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
            if canvas_data is None or canvas_data == {}:
                return CanvasState()

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

        try:
            canvas = CanvasState(**canvas_data)
        except (TypeError, ValueError) as e:
            logger.error(
                f"Canvas con schema non valido per {project_id}: {e}",
                op="get_project_canvas",
            )
            reason_str = str(e)
            if len(reason_str) > 300:
                reason_str = reason_str[:297] + "..."
            raise CanvasCorruptedError(
                project_id=project_id,
                reason=f"schema non valido ({reason_str})",
                original_error=e,
            )
        try:
            # B4: la schema inference e' di projects — la iniettiamo noi,
            # output_contract non importa piu' questo dominio.
            from app.core.projects.schema_inference import SchemaInferenceService

            sync_canvas_with_output_contracts(
                canvas=canvas,
                project_id=project_id,
                project_service=self._owner,
                schema_inference_factory=SchemaInferenceService,
            )
        except Exception as e:
            logger.warning(
                f"sync output_contract canvas fallito per {project_id}: {e}",
                op="get_project_canvas",
                exc_info=True,
            )
        return canvas

    def get_project_raw_canvas(
        self,
        project_id: str,
        group_id: Optional[str] = None,
    ) -> Optional[RawProjectCanvas]:
        """Read the persisted canvas without JSON/schema validation.

        This is the recovery boundary for corrupted canvases. Normal project
        reads must continue to use `get_project_canvas`, which fails loud on
        malformed JSON or invalid CanvasState shape. When `group_id` is
        provided the DB read is tenant-filtered, so service callers cannot
        accidentally bypass project ownership checks.
        """
        row = project_db.get_project_raw_canvas_row(project_id, group_id=group_id)
        if not row:
            return None
        return RawProjectCanvas(project_id=row.id, canvas=row.canvas)

    def update_canvas(self, project_id: str, canvas: CanvasState) -> bool:
        """Salva canvas e aggiorna updated_at. False se il progetto non esiste.

        Invalida lo stato di validazione (Epic CanvasVal §12): salvare il canvas
        azzera ``canvas_validated`` del progetto E lo cascata a False su tutti i
        progetti a valle (manipola/arricchisci che lo usano come ``project_source``),
        perché un cambio a monte può invalidarne la catena certificata.

        Raises:
            ProjectLineageCycleError: se i `project_source` nel canvas +
                le dipendenze transitive degli altri progetti formano un
                ciclo. Il router traduce in HTTP 400.
        """
        row = project_db.get_project_row(project_id)
        if not row:
            return False

        self._owner._assert_no_lineage_cycle(project_id, canvas)

        updated_row = project_db.update_project_row(
            project_id, canvas=canvas.model_dump(), canvas_validated=False
        )
        if updated_row is None:
            return False

        # Cascade: ogni progetto a valle perde la certificazione (la sua catena
        # include questo canvas, ora cambiato). Idempotente (set a False).
        for dep in self._owner._find_dependent_projects(
            project_id, group_id=row.group_id
        ):
            project_db.update_project_row(dep["id"], canvas_validated=False)

        return True

    def set_canvas_validated(self, project_id: str, value: bool) -> bool:
        """Persisti lo stato di validazione canvas (Epic CanvasVal §12).

        Chiamato da POST /validate con ``not report.has_errors`` → lo stato
        memorizzato riflette sempre l'esito dell'ultima validazione. False se il
        progetto non esiste.
        """
        if not project_db.get_project_row(project_id):
            return False
        project_db.update_project_row(project_id, canvas_validated=value)
        return True

    # ── Versioning canvas (#12) ──────────────────────────────────────────

    def create_canvas_version(
        self,
        project_id: str,
        label: Optional[str] = None,
        group_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Optional[CanvasVersionRef]:
        """Salva uno snapshot del canvas corrente. None se il progetto/canvas
        non esiste. Mantiene al più le 30 versioni più recenti."""
        canvas = self._owner.get_project_canvas(project_id)
        if canvas is None:
            return None
        version_id = uuid.uuid4().hex
        clean_label = (label or "").strip()[:120] or None
        project_db.insert_canvas_version(
            version_id, project_id, group_id, clean_label,
            canvas.model_dump(), created_by,
        )
        project_db.prune_canvas_versions(project_id, keep=30)
        return CanvasVersionRef(id=version_id, label=clean_label)

    def list_canvas_versions(self, project_id: str) -> list[CanvasVersionRef]:
        return [
            CanvasVersionRef(
                id=str(r.get("id")),
                label=r.get("label"),
                created_at=r.get("created_at"),
                created_by=r.get("created_by"),
            )
            for r in project_db.list_canvas_versions(project_id)
        ]

    def restore_canvas_version(
        self, project_id: str, version_id: str
    ) -> Optional[CanvasState]:
        """Ripristina il canvas del progetto a una versione salvata. None se la
        versione non esiste. Passa per update_canvas → resetta canvas_validated
        e cascata ai progetti a valle (come ogni salvataggio)."""
        row = project_db.get_canvas_version(version_id, project_id)
        if row is None:
            return None
        raw = row.get("canvas")
        canvas_dict = raw if isinstance(raw, dict) else json.loads(raw)
        canvas_dict = {k: v for k, v in canvas_dict.items() if not str(k).startswith("_")}
        canvas = CanvasState(**canvas_dict)
        self._owner.update_canvas(project_id, canvas)
        return canvas


__all__ = ["ProjectCanvasService", "RawProjectCanvas"]
