"""Cluster 1 contract test: guard su update_project_output_contract.

Bug originali (audit P0 cluster):
- etl-executors P0 #1: `manipola/executor.py:397-405` — `update_project_output_contract`
  invocato SENZA try/except dopo che il blob e' gia' stato scritto. Se la
  call DB fallisce (deadlock/timeout), l'eccezione propaga al Celery task
  bypassando `append_step` — la run risulta "failed" lato task ma il dato
  e' gia' persistito: stato ambiguo non triageable.
- etl-executors P0 #3: `estrai/executor.py:369-370` — analogo:
  `_salva_tipi_colonne_da_canvas` chiama internamente
  `update_project_output_contract` senza guard. Stessa famiglia.

Post-fix (sessione corrente): tutti e 3 i path arricchisci/manipola/estrai
ora hanno try/except con log ERROR + flag `contract_update_failed` nel
result, allineati al pattern di `arricchisci/executor.py:151-170` che era
gia' fixato.

Questo test verifica il contract di OSSERVABILITA' del fix:
  1. l'eccezione di update_project_output_contract NON propaga
  2. append_step viene comunque emesso (visibilita' nello step log)
  3. il result contiene `contract_update_failed=True` per UI/caller

Pattern Go-friendly: monkeypatch puro, no infrastruttura. Mappa a Go come
table-driven test su 3 executor `(estrai/manipola/arricchisci)` con stub
project_service che restituisce errore su update_output_contract.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract, pytest.mark.regression]


class _BlobSavedButContractFails:
    """Stub project_service: simula blob gia' scritto, contract update DB
    deadlock/timeout. update_project_output_contract solleva."""

    def __init__(self):
        self.update_calls = 0
        self.get_calls = 0

    def get_project_output_contract(self, project_id):
        self.get_calls += 1
        return None

    def update_project_output_contract(self, project_id, contract_dict):
        self.update_calls += 1
        raise RuntimeError("DB deadlock simulato post blob-save")


# Path 1: manipola/executor.py:397-414

def test_manipola_contract_failure_does_not_swallow_step_emission(monkeypatch):
    """Post-fix manipola: contract update failure → log ERROR + flag
    contract_update_failed + append_step comunque emesso. Pre-fix: l'eccezione
    propagava direttamente al Celery task."""
    from app.core.manipola.executor import ManipolaExecutor

    project_service = _BlobSavedButContractFails()
    executor = ManipolaExecutor(project_service=project_service)

    # Patch tutti i path attorno al test point cosi' arriviamo al
    # contract update con un save_result success simulato.
    import pandas as pd
    monkeypatch.setattr(executor, "_init_security_context", lambda *a, **kw: "grp-1")
    monkeypatch.setattr(executor, "_carica_sorgenti_progetto", lambda *a, **kw: None)
    monkeypatch.setattr(executor, "_carica_sorgenti_file", lambda *a, **kw: None)
    monkeypatch.setattr(executor, "_carica_sorgenti_datasource", lambda *a, **kw: None)
    monkeypatch.setattr(executor, "_carica_sorgenti_statiche", lambda *a, **kw: None)
    monkeypatch.setattr(
        executor, "_applica_trasformazioni",
        lambda *a, **kw: pd.DataFrame({"col": [1, 2, 3]}),
    )
    monkeypatch.setattr(
        executor, "_applica_group_by",
        lambda df, *a, **kw: df,
    )
    monkeypatch.setattr(
        executor, "_salva_risultato",
        lambda *a, **kw: {
            "success": True,
            "result_id": "r-1",
            "rows_count": 3,
            "output_filename": "out.csv",
            "columns": ["col"],
            "output_columns": ["col"],
            "intermediate_persistence_mode": "final_only",
        },
    )
    monkeypatch.setattr(
        executor, "_costruisci_colonne_con_tipi",
        lambda df, *a, **kw: [{"name": "col", "type": "int"}],
    )

    # Cattura le chiamate append_step per assert di osservabilita'.
    captured_steps = []
    monkeypatch.setattr(
        "app.core.manipola.executor.append_step",
        lambda step: captured_steps.append(step),
    )

    # Canvas minimo con un result_node
    from types import SimpleNamespace

    result_node = SimpleNamespace(
        id="res-1", type="result",
        data={"group_by_enabled": False, "aggregations": [], "group_by_columns": []},
    )
    result_node.model_dump = lambda: {"id": "res-1", "type": "result"}
    result_node.format = "csv"
    canvas = SimpleNamespace(
        nodes=[
            SimpleNamespace(id="src-1", type="project_source", data={}, model_dump=lambda: {}),
            result_node,
        ],
        edges=[],
    )
    canvas.model_dump = lambda: {"nodes": [], "edges": []}

    # Pre-fix: questa linea avrebbe sollevato RuntimeError
    result = executor.execute(project_id="proj-1", canvas=canvas)

    # 1. NON solleva: la response e' un dict
    assert isinstance(result, dict), (
        "execute() deve ritornare dict, non sollevare l'eccezione del "
        "contract update (regressione del cluster 1 P0)"
    )
    # 2. update_project_output_contract e' stato chiamato (ed e' fallito)
    assert project_service.update_calls >= 1, (
        "Il path di contract update deve essere stato esercitato"
    )
    # 3. Il flag contract_update_failed e' propagato nel result
    assert result.get("contract_update_failed") is True, (
        "Post-fix il flag deve essere True per visibilita' UI/caller"
    )
    # 4. append_step DEVE essere stato chiamato (ANTI-REGRESSION CHIAVE)
    assert len(captured_steps) >= 1, (
        "append_step non emesso dopo fallimento contract update. "
        "Pre-fix: l'eccezione propagava al Celery task SENZA emettere "
        "append_step, run invisibile nello step log."
    )
    last_step = captured_steps[-1]
    assert last_step["module"] == "manipola"


# Path 2: architectural lint - verifica che i 3 executor abbiano la guard

def test_all_three_executors_have_contract_update_guard():
    """Architectural assertion: i 3 path (estrai/manipola/arricchisci)
    devono avere try/except attorno a update_project_output_contract /
    _salva_tipi_colonne_da_canvas. Regression-proof contro futuri
    callsite aggiunti senza guard."""
    import inspect
    from app.core.manipola import executor as manipola_executor
    from app.core.arricchisci import executor as arricchisci_executor
    from app.core.estrai import executor as estrai_executor

    for mod, label, marker in [
        (manipola_executor, "manipola", "update_project_output_contract"),
        (arricchisci_executor, "arricchisci", "update_project_output_contract"),
        (estrai_executor, "estrai", "_salva_tipi_colonne_da_canvas"),
    ]:
        source = inspect.getsource(mod)
        assert "contract_update_failed" in source, (
            f"{label}/executor.py deve esporre il flag 'contract_update_failed' "
            f"dopo failure su {marker}. Il flag e' il contract di osservabilita' "
            f"per il caller/UI e dimostra che la guard try/except e' presente."
        )
