"""
Eccezioni custom condivise tra i moduli backend.
"""

from typing import List, Dict, Any


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
