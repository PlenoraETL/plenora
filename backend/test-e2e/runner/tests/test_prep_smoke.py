"""Smoke test del docker stack — prerequisito di tutti gli altri test.

Catalogo: PREP-001.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.smoke
@pytest.mark.profile_core
def test_prep_001_stack_healthy(stack_healthy: dict[str, Any]) -> None:
    """PREP-001 — Tutti i container del profilo `core` sono (healthy).

    Asserzione minima: la fixture `stack_healthy` ha già skippato se i
    container non erano in piedi, qui verifichiamo che almeno i 3 servizi
    del profilo `core` siano riconosciuti.
    """
    assert stack_healthy["status"] == "ok"

    services = stack_healthy["services"]
    expected_core = {"postgres", "redis", "fake-rest"}
    found = set(services.keys())

    missing = expected_core - found
    assert not missing, (
        f"Servizi core mancanti: {missing}. Trovati: {sorted(found)}"
    )

    not_healthy = [s for s, info in services.items() if not info["healthy"]]
    assert not not_healthy, f"Servizi non healthy: {not_healthy}"
