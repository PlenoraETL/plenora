"""Cluster 2 contract test: append_step emitted su early-return (estrai + carica).

Bug originali (audit P0/P1 cluster):
- etl-executors P0 #2: `estrai/executor.py:285-305` — 4 early-return
  uscivano da `execute()` PRIMA del blocco `append_step` (riga 316).
  Tutte le failure pre-connessione (result_node missing,
  default_connection_id missing, CredentialUnavailableError, ValueError
  da _resolve_connection) erano invisibili nel run step log.
- etl-executors P1 #2: `carica/executor.py` non emetteva MAI `append_step`.
  Ogni run di scrittura DB (success o failure) era invisibile nello
  step log.

Post-fix:
- estrai: helper `_failure_step` single-exit-point applicato a tutti i
  4 early-return + step emit alla fine in success path.
- carica: import `append_step` aggiunto, helper `_failure_step` per i 5
  early-return (SKIPPED_IDEMPOTENT, VALIDATION_ERROR config, CONNECTION_ERROR
  get_connection, CONNECTION_ERROR create_engine, VALIDATION_ERROR validate)
  + step emit finale con discriminazione success/partial/failure.

Pattern Go-friendly: `@pytest.mark.parametrize` su 3 scenari estrai +
table-driven equivalent per carica. Mappa a Go come test table-driven
con `tc := struct{name, patchFunc, expectedFragment string}{...}`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# Path 1: estrai/executor.py early-return scenarios

@pytest.fixture
def captured_steps(monkeypatch):
    steps = []
    monkeypatch.setattr("app.core.estrai.executor.append_step", lambda s: steps.append(s))
    return steps


def test_estrai_result_node_missing_emits_failure_step(monkeypatch, captured_steps):
    """Pre-fix: return immediato senza append_step. Post-fix: failure step emesso."""
    from app.core.estrai.executor import EstraiExecutor

    executor = EstraiExecutor(
        project_service=MagicMock(),
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
    )

    # Canvas senza result node
    canvas = SimpleNamespace(
        nodes=[SimpleNamespace(id="src-1", type="project_source", data={})],
        edges=[],
    )
    result = executor.execute("proj-1", canvas)

    assert result["success"] is False
    assert "risultato" in result["message"].lower()
    assert len(captured_steps) == 1, (
        "append_step deve essere emesso anche su early-return "
        "(pre-fix: zero step emessi su result_node missing)"
    )
    assert captured_steps[0]["status"] == "failure"
    assert captured_steps[0]["module"] == "estrai"


def test_estrai_connection_missing_emits_failure_step(monkeypatch, captured_steps):
    """Pre-fix: return immediato senza append_step su default_connection_id None."""
    from app.core.estrai.executor import EstraiExecutor

    project_service = MagicMock()
    project_service.get_project_metadata.return_value = SimpleNamespace(
        default_connection_id=None,
    )
    executor = EstraiExecutor(
        project_service=project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
    )

    canvas = SimpleNamespace(
        nodes=[
            SimpleNamespace(id="src-1", type="project_source", data={}),
            SimpleNamespace(id="res-1", type="result", data={}),
        ],
        edges=[],
    )
    result = executor.execute("proj-1", canvas)

    assert result["success"] is False
    assert "connessione" in result["message"].lower()
    assert len(captured_steps) == 1
    assert captured_steps[0]["status"] == "failure"


def test_estrai_credential_unavailable_emits_failure_step_with_user_error(monkeypatch, captured_steps):
    """Pre-fix: CredentialUnavailableError → return user_error senza step.
    Post-fix: step emesso + user_error=True propagato nel result."""
    from app.core.estrai.executor import EstraiExecutor
    from app.infrastructure.security.crypto import CredentialUnavailableError

    project_service = MagicMock()
    project_service.get_project_metadata.return_value = SimpleNamespace(
        default_connection_id="conn-1",
    )
    executor = EstraiExecutor(
        project_service=project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
    )
    monkeypatch.setattr(
        executor, "_resolve_connection",
        lambda conn_id: (_ for _ in ()).throw(CredentialUnavailableError("conn-1")),
    )

    canvas = SimpleNamespace(
        nodes=[
            SimpleNamespace(id="src-1", type="project_source", data={}),
            SimpleNamespace(id="res-1", type="result", data={}),
        ],
        edges=[],
    )
    result = executor.execute("proj-1", canvas)

    assert result["success"] is False
    assert result.get("user_error") is True
    assert "credenziale" in result["message"].lower()
    assert len(captured_steps) == 1
    assert captured_steps[0]["status"] == "failure"


# Path 2: carica/executor.py — verifica che append_step venga emesso

def _capture_carica_steps(monkeypatch):
    steps = []
    monkeypatch.setattr("app.core.carica.executor.append_step", lambda s: steps.append(s))
    return steps


def test_carica_validation_error_emits_failure_step(monkeypatch):
    """Pre-fix: carica/executor.py NON emetteva mai append_step. Post-fix:
    failure step emesso su VALIDATION_ERROR (config parsing fallito)."""
    from app.core.carica.executor import CaricaExecutor
    from app.core.carica.models import WriteStatus

    captured = _capture_carica_steps(monkeypatch)
    executor = CaricaExecutor(connection_service=MagicMock())

    # Forziamo WriteConfig.from_node_data a sollevare → VALIDATION_ERROR path
    def _raise_validation(data):
        raise ValueError("Config invalida (test forced)")
    monkeypatch.setattr(
        "app.core.carica.executor.WriteConfig.from_node_data",
        staticmethod(_raise_validation),
    )

    df = pd.DataFrame({"a": [1, 2]})
    result = executor.execute(df=df, node_data={}, project_id="proj-1")

    assert result.status == WriteStatus.VALIDATION_ERROR
    assert len(captured) == 1, (
        "Post-fix: carica deve emettere step. Pre-fix: zero step emessi "
        "per qualsiasi run CARICA (success o failure)."
    )
    assert captured[0]["module"] == "carica"
    assert captured[0]["status"] == "failure"


def test_carica_skipped_idempotent_emits_skipped_step(monkeypatch):
    """SKIPPED_IDEMPOTENT non e' failure: status step deve essere 'skipped'
    per non inquinare le metriche di failure UI."""
    from app.core.carica.executor import CaricaExecutor
    from app.core.carica.models import WriteStatus

    captured = _capture_carica_steps(monkeypatch)
    executor = CaricaExecutor(connection_service=MagicMock())
    monkeypatch.setattr(
        "app.core.intermediate.run_idempotency.is_already_completed",
        lambda run_id: True,
    )

    df = pd.DataFrame({"a": [1, 2]})
    result = executor.execute(df=df, node_data={}, project_id="proj-1", run_id="run-1")

    assert result.status == WriteStatus.SKIPPED_IDEMPOTENT
    assert len(captured) == 1
    assert captured[0]["status"] == "skipped", (
        "SKIPPED_IDEMPOTENT non deve essere classificato come failure"
    )


# Path 3: architectural assertion

def test_estrai_and_carica_executors_have_append_step_usage():
    """Architectural lint: i 2 executor devono importare e usare append_step.
    Anti-regression contro qualcuno che rimuove l'import in futuro."""
    import inspect
    from app.core.estrai import executor as estrai_executor
    from app.core.carica import executor as carica_executor

    for mod, label in [(estrai_executor, "estrai"), (carica_executor, "carica")]:
        source = inspect.getsource(mod)
        assert "append_step" in source, (
            f"{label}/executor.py deve usare append_step per emettere step log. "
            f"Senza, le run di {label} sono invisibili nel run step log UI."
        )
        assert "_failure_step" in source, (
            f"{label}/executor.py deve avere helper _failure_step per "
            f"single-exit-point — anti-regression contro early-return che "
            f"bypassano append_step."
        )
