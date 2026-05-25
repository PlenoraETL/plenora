"""
Contract test per `ExecutionManager._execute_carica` — registrazione
ciclo di vita della run CARICA su `intermediate.runs`.

Cluster bug coperto (commit 982f718e): pre-fix lo scheduler passava
un `carica_run_id` deterministico (`schedule-{id}-{scheduled_for}-
carica`) al `CaricaExecutor.execute(run_id=...)` ma non creava mai
la riga corrispondente in `intermediate.runs` ne' la chiudeva. Il
guard `is_already_completed(run_id)` ritornava sempre `False` →
l'executor riscriveva ad ogni replay dello stesso tick scheduled.

Post-fix:
  - `get_run + create_run` PRIMA dell'execute
  - `update_run(status="completed" if success else "failed")` DOPO
  - `update_run(status="failed")` nell'except handler

Architectural-test puro: verifichiamo la presenza dei pattern
nel source senza istanziare ExecutionManager (mocking setup pesante,
e i flow sono coperti da `scheduler-carica-runtime.spec.js` end-to-end).
"""

from __future__ import annotations

import inspect

import pytest

from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def execute_carica_source() -> str:
    """Source del metodo `_execute_carica` come stringa per AST-lint."""
    return inspect.getsource(ExecutionManager._execute_carica)


class TestRunIdComputation:
    """`carica_run_id` deve essere calcolato fuori dal try cosi' e'
    visibile anche nell'except handler (per marcare la run come
    failed)."""

    def test_carica_run_id_computed_before_try_block(
        self, execute_carica_source
    ):
        src = execute_carica_source
        # Trova posizione di `carica_run_id =` e del `try:`
        i_carica_run_id = src.find("carica_run_id = (")
        i_try = src.find("\n        try:")
        assert i_carica_run_id != -1, (
            "regressione: carica_run_id non definito"
        )
        assert i_try != -1
        assert i_carica_run_id < i_try, (
            "regressione: carica_run_id definito DENTRO il try → "
            "non e' visibile nell'except per marcare la run failed"
        )

    def test_carica_run_id_pattern_deterministic(self, execute_carica_source):
        """La key deve includere `schedule_id` + `scheduled_for` per
        essere idempotency-stable cross-tick."""
        src = execute_carica_source
        assert "f\"schedule-{config.id}-{scheduled_for}-carica\"" in src, (
            "regressione: pattern carica_run_id cambiato — la "
            "idempotency key cross-retry potrebbe non essere piu' "
            "deterministica"
        )


class TestRunLifecycleOpenBeforeExecute:
    """`runs_db.create_run` deve essere chiamato PRIMA dell'execute
    cosi' il guard `is_already_completed` puo' scattare al replay."""

    def test_create_run_called_before_execute(self, execute_carica_source):
        src = execute_carica_source
        i_create = src.find("intermediate_run_lifecycle.create_run(")
        i_execute = src.find("carica_executor.execute(")
        assert i_create != -1, (
            "regressione: intermediate_run_lifecycle.create_run "
            "rimosso da _execute_carica — il guard idempotency "
            "non scattera' al replay (bug originale)"
        )
        assert i_execute != -1
        assert i_create < i_execute, (
            "regressione ordine: create_run DEVE precedere execute. "
            "Altrimenti il primo tick non lascia traccia e il guard "
            "non scatta al secondo."
        )

    def test_get_run_check_before_create(self, execute_carica_source):
        """Idempotente: get_run prima del create per non duplicare in
        caso di retry intra-tick."""
        src = execute_carica_source
        i_get = src.find("intermediate_runs_db.get_run(carica_run_id)")
        i_create = src.find("intermediate_run_lifecycle.create_run(")
        assert i_get != -1 and i_create != -1
        assert i_get < i_create


class TestRunLifecycleCloseAfterExecute:
    """`runs_db.update_run(status=...)` deve essere chiamato DOPO
    l'execute in entrambi i path (success + failure)."""

    def test_update_run_after_execute_success_path(
        self, execute_carica_source
    ):
        src = execute_carica_source
        # Cerca update_run con status condizionale completed/failed
        assert 'status="completed" if success else "failed"' in src, (
            "regressione: update_run nel success path non setta lo "
            "status condizionale completed/failed. Il guard "
            "is_already_completed potrebbe non scattare al replay."
        )

    def test_update_run_failed_in_except_handler(
        self, execute_carica_source
    ):
        """Best-effort: anche nell'except handler la run deve essere
        marcata failed cosi' un crash a meta' run non lascia status
        'running' permanente (che bloccherebbe i replay successivi)."""
        src = execute_carica_source
        # Cerca pattern: dopo `except Exception as e:` deve esserci
        # un update_run(..., status="failed")
        except_pos = src.find("except Exception as e:")
        assert except_pos != -1
        post_except = src[except_pos:]
        assert "update_run(" in post_except, (
            "regressione: except handler non chiama update_run — "
            "una run crashata resta 'running' indefinitamente, i "
            "replay successivi vengono bloccati"
        )
        assert 'status="failed"' in post_except, (
            "regressione: except handler non setta status='failed'"
        )


class TestFailOpenOnIntermediateError:
    """Gli errori sull'intermediate DB NON devono bloccare la
    scrittura CARICA principale (fail-open con warning)."""

    def test_create_run_wrapped_in_try_except(self, execute_carica_source):
        """`create_run` deve essere dentro try/except con
        logger.warning, non sollevare verso il caller."""
        src = execute_carica_source
        # Trova la sezione che apre la run
        i_create = src.find("intermediate_run_lifecycle.create_run(")
        assert i_create != -1
        # Cerca il try che la avvolge nelle ~10 righe precedenti
        prev_lines = src[max(0, i_create - 400):i_create]
        assert "try:" in prev_lines, (
            "regressione: create_run non in try/except — un errore "
            "DB sull'intermediate blocca la scrittura CARICA principale"
        )
        # E un warning nel except sotto
        post_create = src[i_create:i_create + 1500]
        assert "logger.warning" in post_create, (
            "regressione: nessun warning sul fallimento create_run "
            "— l'errore silente rende debugging impossibile"
        )

    def test_update_run_close_wrapped_in_try_except(
        self, execute_carica_source
    ):
        """Stesso pattern fail-open per update_run."""
        src = execute_carica_source
        i_update = src.find('status="completed" if success else "failed"')
        assert i_update != -1
        prev_lines = src[max(0, i_update - 400):i_update]
        assert "try:" in prev_lines


class TestImportsExist:
    """Smoke-import: i moduli usati per il lifecycle devono essere
    importabili (no broken refactoring)."""

    def test_run_lifecycle_module_importable(self):
        from app.core.intermediate import run_lifecycle
        assert hasattr(run_lifecycle, "create_run")

    def test_runs_db_get_and_update_importable(self):
        from app.core.intermediate import runs_db
        assert hasattr(runs_db, "get_run")
        assert hasattr(runs_db, "update_run")
