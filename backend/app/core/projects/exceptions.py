"""Domain exceptions for projects."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class ProjectLineageCycleError(Exception):
    """Raised when a cross-project dependency cycle is detected."""

    def __init__(self, chain: List[str]):
        self.chain = chain
        super().__init__(self._format(chain))

    @staticmethod
    def _format(chain: List[str]) -> str:
        arrow = " → "
        return f"Ciclo di dipendenza tra progetti: {arrow.join(chain)}"


class CanvasCorruptedError(Exception):
    """Raised when a persisted project canvas cannot be parsed safely."""

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
    """Raised when a project cannot be deleted because dependents exist."""

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
    """Raised when critical project cascade cleanup steps fail."""

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


class ColumnsMetadataSaveError(Exception):
    """Raised when saving project canvas columns_metadata fails permanently."""

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


__all__ = [
    "CanvasCorruptedError",
    "ColumnsMetadataSaveError",
    "ProjectCleanupPartialError",
    "ProjectHasDependenciesError",
    "ProjectLineageCycleError",
]
