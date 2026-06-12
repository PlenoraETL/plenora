"""Sub-service output contract progetto (Epic F roadmap, fetta F4).

Lettura/aggiornamento dell'output contract come interfaccia stretta,
estratta dalla facade ``ProjectService``. Nessun hook storico coinvolto:
il sub-service e' autonomo (solo leaf db). I chiamanti nuovi dipendono
da questa classe; la facade delega e resta compatibile.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.projects import db as project_db


class ProjectOutputContractService:
    """Letture/scritture dell'output contract di un progetto."""

    def get_project_output_contract(
        self,
        project_id: str,
    ) -> Mapping[str, object] | None:
        row = project_db.get_project_row(project_id)
        if not row:
            return None
        contract = row.output_contract
        return contract if isinstance(contract, Mapping) else None

    def update_project_output_contract(
        self,
        project_id: str,
        output_contract: Mapping[str, object],
    ) -> bool:
        return project_db.update_project_output_contract_row(
            project_id,
            output_contract=output_contract,
        ) is not None


__all__ = ["ProjectOutputContractService"]
