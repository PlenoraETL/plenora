"""Characterization #28-B (setup): divergenza standard vs eseguibile nel
SETUP di execute_enrichment_task (auth recheck, sorgente group_id, intermediate
run creation). Pinna il comportamento CORRENTE per coprire il refactor verso
EnrichmentRunMode (increment 1). L'harness aborta sul primo loader, quindi
osserviamo solo il setup (finalize/output coperti separatamente).
"""

from __future__ import annotations

import pytest

from tests2._helpers.enrichment_task_harness import (
    install_enrichment_task_mocks,
    invoke_enrichment_task,
)
from tests2._helpers.enrichment_task_fixtures import make_minimal_source_canvas


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


# Concern: sorgente group_id + intermediate run creation


def test_setup_standard_resolves_group_via_project_and_creates_run(monkeypatch):
    spies = install_enrichment_task_mocks(monkeypatch, project_group_id="g-std")

    invoke_enrichment_task(
        project_id="proj-std",
        canvas=make_minimal_source_canvas(),
        is_eseguibile=False,
    )

    # standard: group_id da ProjectService + un intermediate run creato
    assert spies.project_metadata_calls == ["proj-std"]
    assert len(spies.intermediate_run_creates) == 1
    assert spies.eseguibile_lookups == []


def test_setup_eseguibile_resolves_group_via_eseg_db_and_skips_run(monkeypatch):
    spies = install_enrichment_task_mocks(monkeypatch, eseguibile_group_id="g-eseg")

    invoke_enrichment_task(
        project_id="exec-1",
        canvas=make_minimal_source_canvas(),
        is_eseguibile=True,
    )

    # eseguibile: group_id dalla tabella eseguibili, NESSUN intermediate run
    assert spies.eseguibile_lookups == ["exec-1"]
    assert spies.intermediate_run_creates == []


def test_setup_eseguibile_uses_contract_tenant_without_db_lookup(monkeypatch):
    spies = install_enrichment_task_mocks(monkeypatch, eseguibile_group_id=None)
    canvas = make_minimal_source_canvas()

    invoke_enrichment_task(
        project_id="exec-1",
        canvas=canvas,
        is_eseguibile=True,
        execution_contract={
            "schema_version": 2,
            "project_id": "exec-1",
            "project_type": "arricchisci",
            "group_id": "g-contract",
            "canvas": canvas,
            "artifact_refs": {},
            "execution_subject": {
                "kind": "eseguibile",
                "id": "exec-1",
                "version_id": "version-1",
                "run_id": "run-1",
            },
        },
    )

    assert spies.eseguibile_lookups == []
    assert spies.intermediate_run_creates == []


# Concern: auth recheck (standard si, eseguibile no)


def test_setup_auth_recheck_blocks_standard(monkeypatch):
    revoked = {"status": "revoked", "success": False, "error": "permesso revocato"}
    install_enrichment_task_mocks(monkeypatch, revoked_payload=revoked)

    res = invoke_enrichment_task(
        project_id="proj-std",
        canvas=make_minimal_source_canvas(),
        is_eseguibile=False,
    )

    # standard fa il recheck → ritorna subito il payload revocato
    assert res.get("status") == "revoked"


def test_setup_auth_recheck_skipped_for_eseguibile(monkeypatch):
    revoked = {"status": "revoked", "success": False, "error": "permesso revocato"}
    install_enrichment_task_mocks(monkeypatch, revoked_payload=revoked)

    res = invoke_enrichment_task(
        project_id="exec-1",
        canvas=make_minimal_source_canvas(),
        is_eseguibile=True,
    )

    # eseguibile NON fa il recheck → non ritorna il payload revocato
    # (procede e fallisce sul loader = error dict dell'harness)
    assert res.get("status") != "revoked"
