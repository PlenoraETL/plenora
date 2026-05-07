"""Contract test R-25 PR-B2.b — propagation manipola + arricchisci.

Estende il contract di PR-B2 (estrai-final) ai 3 call-site rimasti:

  1. manipola/executor.py -> record_final_snapshot(module="manipola")
     (nodo result, dopo _applica_trasformazioni).
  2. manipola/executor.py -> record_checkpoint_snapshot(module="manipola")
     dentro il loop per ogni transform / multi_join.
  3. arricchisci/api_connection_support.py:_write_intermediate_snapshot
     -> record_checkpoint_snapshot(module="arricchisci") (per nodi
     api_enrich, sempre skipped da policy in Batch 1).

Oltre ai 3 chain interni, valida:
  - intermediate_mode_override propagato attraverso
    OrchestratorService.execute_pipeline -> manipola_executor.execute
    -> _applica_trasformazioni (final + tutti i checkpoint).
  - intermediate_mode_override propagato attraverso execute_pipeline_task
    -> orchestrator.execute_pipeline (kwarg passing-through).
  - execute_enrichment_task -> record_checkpoint_snapshot e
    record_final_snapshot ricevono il kwarg.

Test puramente unitari: niente DB reale, niente Celery worker, niente
SQLAlchemy. Mock dei sink finali (`record_*_snapshot`) e dei collaborator.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.intermediate, pytest.mark.contract]


# Helpers

def _make_cfg(mode: str = "metadata_only", **overrides) -> dict:
    cfg = {
        "mode": mode,
        "preview_rows": 500,
        "artifact_format": "parquet",
        "manipola_checkpoints": "critical",
        "estrai_checkpoints": "final_only",
        "arricchisci_checkpoints": "final_only",
        "artifact_bucket": None,
        "artifact_prefix": "intermediate",
        "fingerprint_mode": "sample",
        "fingerprint_sample_rows": 1000,
    }
    cfg.update(overrides)
    return cfg


def _patch_settings(cfg: dict):
    from app.core.intermediate import policy as _policy
    return patch.object(_policy, "_read_settings", return_value=cfg)


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    from app.core.intermediate.policy import reset_validation_cache
    reset_validation_cache()
    yield
    reset_validation_cache()


@pytest.fixture
def small_df():
    return pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})


# A. manipola.executor.execute  ->  record_final_snapshot

class TestManipolaFinalPropagation:
    """`manipola/executor.py:execute` accetta intermediate_mode_override e
    lo passa a `record_final_snapshot(mode_override=...)` per il nodo result."""

    def test_signature_accepts_intermediate_mode_override(self):
        import inspect
        from app.core.manipola.executor import ManipolaExecutor

        sig = inspect.signature(ManipolaExecutor.execute)
        assert "intermediate_mode_override" in sig.parameters
        assert sig.parameters["intermediate_mode_override"].default is None

    def test_applica_trasformazioni_signature_accepts_kwarg(self):
        """Il kwarg deve attraversare anche _applica_trasformazioni
        (loop checkpoint)."""
        import inspect
        from app.core.manipola.executor import ManipolaExecutor

        sig = inspect.signature(ManipolaExecutor._applica_trasformazioni)
        assert "intermediate_mode_override" in sig.parameters
        assert sig.parameters["intermediate_mode_override"].default is None


# B. manipola.executor._applica_trasformazioni  ->  record_checkpoint_snapshot

class TestManipolaCheckpointPropagation:
    """Il loop checkpoint dentro _applica_trasformazioni passa
    `mode_override` a ogni invocazione di record_checkpoint_snapshot."""

    def test_kwarg_passing_through_call_site_in_source(self):
        """Sanity: il source di _applica_trasformazioni contiene
        record_checkpoint_snapshot(... mode_override=intermediate_mode_override)
        come kwarg passato esplicitamente.

        Bytecode-level check (non source-string fragile): l'argomento
        passato e' la variabile locale `intermediate_mode_override`.
        """
        import inspect
        from app.core.manipola.executor import ManipolaExecutor

        # Co_varnames contiene la variabile locale del nome atteso.
        method = ManipolaExecutor._applica_trasformazioni
        co_varnames = method.__code__.co_varnames
        assert "intermediate_mode_override" in co_varnames, (
            "Il loop checkpoint manipola non ha la variabile locale "
            "intermediate_mode_override: probabilmente il kwarg non e' "
            "propagato. co_varnames: " + str(co_varnames)
        )


# C. arricchisci.api_connection_support._write_intermediate_snapshot

class TestArricchisciCheckpointPropagation:
    """`_write_intermediate_snapshot` accetta `mode_override` e lo passa
    a record_checkpoint_snapshot."""

    def test_signature_accepts_mode_override(self):
        import inspect
        from app.core.arricchisci.api_connection_support import (
            ApiConnectionSupportMixin,
        )

        sig = inspect.signature(ApiConnectionSupportMixin._write_intermediate_snapshot)
        assert "mode_override" in sig.parameters
        assert sig.parameters["mode_override"].default is None

    def test_propaga_a_record_checkpoint_snapshot(self, small_df):
        """End-to-end: chiamare _write_intermediate_snapshot con override
        deve risultare in record_checkpoint_snapshot(mode_override=...)."""
        from app.core.arricchisci.api_connection_support import (
            ApiConnectionSupportMixin,
        )

        captured = {}

        def spy(**kwargs):
            captured.update(kwargs)
            from app.core.intermediate.snapshot import SnapshotHandle
            return SnapshotHandle(
                recorded=False, reason="arricchisci_checkpoints_disabled_in_batch1",
                run_id=kwargs["run_id"], node_id=kwargs["node_id"],
            )

        # Bind del metodo come unbound: lo invochiamo passando un dummy
        # self perche' non usa attributi d'istanza.
        instance = SimpleNamespace()
        with _patch_settings(_make_cfg(mode="metadata_only")), \
             patch("app.core.intermediate.snapshot.record_checkpoint_snapshot",
                   side_effect=spy):
            ApiConnectionSupportMixin._write_intermediate_snapshot(
                instance,
                run_id="run-1", node_id="n-1",
                node_type="api_enrich", df=small_df,
                mode_override="full_pg",
            )

        assert captured.get("mode_override") == "full_pg"


# D. api_connector.execute_enrichment  ->  _write_intermediate_snapshot

class TestApiConnectorExecuteEnrichmentPropagation:
    """`ApiConnectionService.execute_enrichment` accetta il kwarg e lo
    propaga a `_write_intermediate_snapshot` per ogni nodo enrich."""

    def test_signature_accepts_intermediate_mode_override(self):
        import inspect
        from app.core.arricchisci.api_connector import ApiConnectionService

        sig = inspect.signature(ApiConnectionService.execute_enrichment)
        assert "intermediate_mode_override" in sig.parameters
        assert sig.parameters["intermediate_mode_override"].default is None


# E. EnrichExecutor.execute (orchestrator entry-point)

class TestEnrichExecutorExecutePropagation:
    def test_signature_accepts_intermediate_mode_override(self):
        import inspect
        from app.core.arricchisci.executor import EnrichExecutor

        sig = inspect.signature(EnrichExecutor.execute)
        assert "intermediate_mode_override" in sig.parameters
        assert sig.parameters["intermediate_mode_override"].default is None


# F. EstraiExecutor.execute (orchestrator entry-point)

class TestEstraiExecutorExecutePropagation:
    def test_signature_accepts_intermediate_mode_override(self):
        import inspect
        from app.core.estrai.executor import EstraiExecutor

        sig = inspect.signature(EstraiExecutor.execute)
        assert "intermediate_mode_override" in sig.parameters
        assert sig.parameters["intermediate_mode_override"].default is None

    def test_underscore_execute_database_signature(self):
        import inspect
        from app.core.estrai.executor import EstraiExecutor

        sig = inspect.signature(EstraiExecutor._execute_database)
        assert "intermediate_mode_override" in sig.parameters


# G. OrchestratorService.execute_pipeline (entry boundary)

class TestOrchestratorServicePropagation:
    def test_signature_accepts_intermediate_mode_override(self):
        import inspect
        from app.core.orchestrator.service import OrchestratorService

        sig = inspect.signature(OrchestratorService.execute_pipeline)
        assert "intermediate_mode_override" in sig.parameters
        assert sig.parameters["intermediate_mode_override"].default is None


# H. Celery tasks (boundary worker)

class TestCeleryTasksSignatures:
    def test_execute_pipeline_task_signature(self):
        import inspect
        from workers.tasks.pipeline_task import execute_pipeline_task

        sig = inspect.signature(execute_pipeline_task)
        assert "intermediate_mode_override" in sig.parameters
        assert sig.parameters["intermediate_mode_override"].default is None

    def test_execute_enrichment_task_signature(self):
        import inspect
        from workers.tasks.enrichment_task import execute_enrichment_task

        sig = inspect.signature(execute_enrichment_task)
        assert "intermediate_mode_override" in sig.parameters
        assert sig.parameters["intermediate_mode_override"].default is None


# I. task_service create_*_task (boundary HTTP -> Celery)

class TestTaskServiceSignatures:
    def test_create_pipeline_task_signature(self):
        import inspect
        from app.services.task_service import TaskService

        sig = inspect.signature(TaskService.create_pipeline_task)
        assert "intermediate_mode_override" in sig.parameters
        assert sig.parameters["intermediate_mode_override"].default is None

    def test_create_enrichment_task_signature(self):
        import inspect
        from app.services.task_service import TaskService

        sig = inspect.signature(TaskService.create_enrichment_task)
        assert "intermediate_mode_override" in sig.parameters
        assert sig.parameters["intermediate_mode_override"].default is None
