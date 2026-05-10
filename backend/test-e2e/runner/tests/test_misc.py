"""BEAT-003, FLOW-001 — Smokes infrastrutturali fuori dal core path.

Questi test richiedono setup multi-container o profili extra
che il singolo runner Plenora dev non offre out-of-the-box.
Sono documentazione eseguibile + skip-safe.
"""

from __future__ import annotations

import os

import httpx
import pytest


API_PREFIX = "/api"


# ── BEAT-003 — Fail-CLOSED 2 beat → 1 leader ──────────────────────────


@pytest.mark.profile_core
@pytest.mark.smoke
def test_beat_003_leader_election_no_double_enqueue() -> None:
    """BEAT-003 — Catalog: "tick attesi N → enqueue conteggio = N
    (non 2N); follower NON enqueue task".

    Richiede 2 container `beat` simultanei sullo stesso Redis,
    con `scheduler_lock` attivo. Plenora dev stack ha 1 solo
    beat (`plenora-beat-1`). Il leader-lock per-Redis e' verificato
    da `app.core.scheduler.scheduler_lock`; il test smoke completo
    necessita di un compose override con `scale: 2` su beat e
    un consumer Redis che conta gli enqueue.

    Skip in profilo `core`: questo smoke vive in un harness
    multi-instance dedicato (TODO: docker-compose.beat-ha.yml).
    """
    if os.getenv("PLENORA_E2E_BEAT_HA_HARNESS") != "1":
        pytest.skip(
            "BEAT-003 richiede 2 beat instances con scheduler_lock. "
            "Set PLENORA_E2E_BEAT_HA_HARNESS=1 e usa il compose "
            "override beat-ha per attivare il test."
        )
    pytest.fail(
        "BEAT-003 harness multi-beat non implementato. "
        "TODO: avviare 2 beat replicas, contare enqueue su Redis, "
        "asserire = N (non 2N)."
    )


# ── FLOW-001 — Scenario completo end-to-end ───────────────────────────


@pytest.mark.smoke
def test_flow_001_end_to_end_scenario(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    """FLOW-001 — Catalog: "Schedule cron → run → estrai postgres
    → arricchisci REST OAuth2 → carica MySQL → notifica → audit".

    Scenario completo che esercita 4 moduli (estrai/arricchisci/
    carica/scheduler) + audit log + notifica. Prereq: profili
    `db` (MySQL) + `core` (postgres + fake-rest) attivi
    contemporaneamente.

    Lo costruiamo sopra agli smoke gia' verdi:
      EXT-001 (postgres source) +
      ENR-001 (OAuth2 enrichment) +
      LOAD-001 (DB target) +
      SCH-001 (schedule manual run) +
      AUTH-001 (audit log entry).

    Skip se profilo `db` (MySQL) non attivo. Quando il harness
    e' completo, costruire una pipeline E2E e verificare:
      - log_runs status=success
      - audit_log entry per ogni transizione
      - target MySQL count match
      - notification_sent=true
    """
    if os.getenv("PLENORA_E2E_PROFILE", "core") not in ("db", "full"):
        pytest.skip(
            "FLOW-001 richiede profilo `db` o `full` (MySQL target). "
            "Il profilo attuale non lo include — il scenario E2E "
            "completo va eseguito in un harness dedicato che ha "
            "tutti i database attivi simultaneamente."
        )
    pytest.fail(
        "FLOW-001 scenario completo non implementato in questo "
        "runner. Smoke individuali coperti: EXT-001, ENR-001, "
        "LOAD-001, SCH-001, AUTH-001. Il glue scenario rimane "
        "TODO."
    )
