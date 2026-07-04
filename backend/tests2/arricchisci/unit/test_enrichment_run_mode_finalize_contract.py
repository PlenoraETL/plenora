"""Characterization #28-B (finalize/output/response): divergenza standard vs
eseguibile a fine di execute_enrichment_task, in run-A-COMPLETAMENTO (final_df).
Pinna il comportamento CORRENTE prima di migrare i concern 5/6/7 in RunMode:
- output: DB (save_pipeline_result_outcome + output_contract) vs S3 (eseg_storage)
- response: result_id vs download_url
- finalize run-logging: standard chiama close_run/append_step/snapshot, eseguibile no.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tests2._helpers.enrichment_task_harness import (
    install_enrichment_task_mocks,
    invoke_enrichment_task,
)
from tests2._helpers.enrichment_task_fixtures import make_source_result_canvas


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


def _df():
    return pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})


def test_standard_run_persists_to_db_with_result_id_and_run_logging(monkeypatch):
    spies = install_enrichment_task_mocks(monkeypatch, final_df=_df())

    res = invoke_enrichment_task(
        project_id="proj-std",
        canvas=make_source_result_canvas(),
        is_eseguibile=False,
        output_format="csv",
    )

    assert res.get("success") is True, res
    # OUTPUT: persistito su DB (pipeline result), NON su S3
    assert len(spies.saved_pipeline_results) == 1
    assert spies.eseg_saves == []
    # output_contract aggiornato (solo standard)
    assert len(spies.output_contract_saves) == 1
    # RESPONSE: result_id valorizzato, nessun download_url
    assert res.get("result_id")
    assert "download_url" not in res
    # FINALIZE run-logging: standard chiama close_run/append_step/snapshot
    assert len(spies.logger_close_runs) == 1
    assert spies.logger_close_runs[0].get("status") == "success"
    assert len(spies.append_steps) == 1
    assert len(spies.final_snapshots) == 1


def test_eseguibile_run_persists_to_storage_with_download_url_no_logging(monkeypatch):
    spies = install_enrichment_task_mocks(monkeypatch, final_df=_df())

    res = invoke_enrichment_task(
        project_id="exec-1",
        canvas=make_source_result_canvas(),
        is_eseguibile=True,
        output_format="csv",
    )

    assert res.get("success") is True, res
    # OUTPUT: persistito su S3 (eseg_storage), NON su DB
    assert len(spies.eseg_saves) == 1
    assert spies.saved_pipeline_results == []
    assert spies.output_contract_saves == []
    # RESPONSE: download_url presente, result_id None
    assert res.get("download_url") == "/api/eseguibili/exec-1/download/{}".format(res["task_id"])
    assert res.get("result_id") is None
    # FINALIZE run-logging: eseguibile NON logga nulla
    assert spies.logger_close_runs == []
    assert spies.append_steps == []
    assert spies.final_snapshots == []
    assert spies.intermediate_close_runs == []


def test_standard_run_failure_closes_run_as_failed_with_debug_payload(monkeypatch):
    # Il persist solleva → outer except → close_failure: il path delicato.
    # Pinna che lo standard logghi il FALLIMENTO (close_run failure +
    # intermediate close success=False + DebugPayloadBuilder), gated da logs_run().
    spies = install_enrichment_task_mocks(
        monkeypatch, final_df=_df(), persist_raises=True,
    )

    res = invoke_enrichment_task(
        project_id="proj-std",
        canvas=make_source_result_canvas(),
        is_eseguibile=False,
        output_format="csv",
    )

    assert res.get("success") is not True, res
    # persist e' stato tentato (e ha sollevato)
    assert len(spies.saved_pipeline_results) == 1
    # FINALIZE failure: close_run con status failure, intermediate close, debug payload
    assert len(spies.logger_close_runs) == 1
    assert spies.logger_close_runs[0].get("status") in ("failure", "error", "failed")
    assert len(spies.intermediate_close_runs) == 1
    assert spies.intermediate_close_runs[0].get("success") is False
    assert len(spies.debug_payloads) == 1
    # success-path logging NON deve essere avvenuto
    assert spies.append_steps == []
    assert spies.final_snapshots == []
