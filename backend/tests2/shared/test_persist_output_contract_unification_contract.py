"""Unificazione: persist output_contract come unica fonte di verita'.

I finalizer di estrai/manipola/arricchisci + enrichment_run_mode riscrivevano
inline la stessa sequenza build(previous)+update+dump dell'output_contract. E'
stata estratta in ``output_contract.persist_output_contract``. Questo contract
test blocca due regressioni:

1. (comportamentale) il helper RIUSA il contratto precedente del progetto
   (id-stability delle colonne attraverso i rename) e PERSISTE il dump.
2. (sorgente) i 4 finalizer DELEGANO al helper e non richiamano piu' inline
   ``update_project_output_contract(`` — cosi' l'invariante ``previous_contract=``
   vive in un solo posto e non puo' essere dimenticato in un call-site.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests2.shared._ast_identifiers import all_identifiers, function_def_names


pytestmark = [pytest.mark.unit, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class _RecordingProjectService:
    """Espone i due metodi che persist_output_contract richiede (duck-typed)."""

    def __init__(self, previous: dict):
        self._previous = previous
        self.get_calls: list[str] = []
        self.updated: tuple | None = None

    def get_project_output_contract(self, project_id: str):
        self.get_calls.append(project_id)
        return self._previous

    def update_project_output_contract(self, project_id: str, dumped: dict):
        self.updated = (project_id, dumped)


def test_persist_reuses_previous_contract_and_persists_dump():
    from app.core.output_contract import persist_output_contract

    # contratto precedente: la colonna sorgente "a" ha id stabile col_x, rev 3
    previous = {
        "revision": 3,
        "columns": [
            {
                "id": "col_x",
                "source_name": "a",
                "physical_name": "a",
                "display_name": "a",
                "type": {"logical": "string"},
                "ordinal": 0,
                "status": "active",
            }
        ],
    }
    ps = _RecordingProjectService(previous)

    dumped = persist_output_contract(
        project_service=ps,
        project_id="p1",
        result_columns=[{"source_name": "a", "name": "a", "type": "str"}],
    )

    # id-stability: ricostruendo la stessa colonna sorgente, l'id resta col_x
    assert dumped["columns"][0]["id"] == "col_x"
    # ha letto il previous contract del progetto giusto
    assert ps.get_calls == ["p1"]
    # ha persistito ESATTAMENTE il dump ritornato (stesso oggetto)
    assert ps.updated == ("p1", dumped)
    # revision incrementata a partire dal previous (3 -> 4)
    assert dumped["revision"] == 4


def test_finalizers_delegate_to_persist_helper():
    finalizers = {
        "estrai": _BACKEND_ROOT / "app" / "core" / "estrai" / "executor.py",
        "manipola": _BACKEND_ROOT / "app" / "core" / "manipola" / "executor.py",
        "arricchisci": _BACKEND_ROOT / "app" / "core" / "arricchisci" / "executor.py",
        "enrichment_run_mode": (
            _BACKEND_ROOT / "app" / "core" / "arricchisci" / "enrichment_run_mode.py"
        ),
    }
    for name, path in finalizers.items():
        src = path.read_text(encoding="utf-8")
        assert "persist_output_contract" in all_identifiers(src), (
            f"{name} non delega a persist_output_contract"
        )
        # nessuna riscrittura inline della sequenza: la CALL a
        # update_project_output_contract( vive solo nel helper condiviso
        assert "update_project_output_contract" not in all_identifiers(src), (
            f"{name} richiama ancora update_project_output_contract inline "
            f"invece di delegare a persist_output_contract"
        )


def test_persist_helper_owns_previous_contract_invariant():
    """Il helper deve passare previous_contract (id-stability) ed essere l'unico
    a contenere la coppia build+update."""
    service_src = (
        _BACKEND_ROOT / "app" / "core" / "output_contract" / "service.py"
    ).read_text(encoding="utf-8")
    assert "persist_output_contract" in function_def_names(service_src)
    # RAW: "previous_contract=" e' un pattern param-def/kwarg-assignment, non puro identificatore
    assert "previous_contract=" in service_src
    assert "get_project_output_contract" in all_identifiers(service_src)
    assert "update_project_output_contract" in all_identifiers(service_src)
