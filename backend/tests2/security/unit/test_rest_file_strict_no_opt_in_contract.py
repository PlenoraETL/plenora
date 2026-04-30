"""Contract test PR-#35 P1-C — REST_FILE shim strict-no-opt-in.

Scope: blinda la decisione di design dietro PR-#35 (SSRF single-source):
il shim REST_FILE `ApiConnectionSourceLoadingMixin._validate_outbound_url`
delega alla primitive `_check_outbound_address(url,
allow_loopback_opt_in=False)`, quindi loopback (IPv4 e IPv6) resta
bloccato anche se l'env opt-in `PLENORA_ALLOW_LOOPBACK_OUTBOUND=1` e'
settata.

Il behavior di `validate_external_url` (raise wrapper, opt-in onorato
in dev) NON cambia: questo file aggiunge un guard test su quella
proprieta' per evitare regressioni accidentali del refactor.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def test_rest_file_shim_blocks_loopback_even_with_env_opt_in(monkeypatch):
    """REST_FILE strict: anche con PLENORA_ALLOW_LOOPBACK_OUTBOUND=1
    settata, il shim deve bloccare 127.0.0.1.

    Garantisce che il pattern `allow_loopback_opt_in=False` passato al
    primitive non venga aggirato da future modifiche."""
    from app.core.arricchisci.api_connection_source_loading import (
        ApiConnectionSourceLoadingMixin,
    )

    # Forza opt-in canonical settata (cosa che farebbe un env di test
    # B-2 functional via conftest.py)
    monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "1")
    # Garantiamo env non hardened (= dev) cosi' isoliamo la prova
    # del solo gating allow_loopback_opt_in.
    monkeypatch.setenv("ENVIRONMENT", "development")

    motivo = ApiConnectionSourceLoadingMixin._validate_outbound_url(
        "http://127.0.0.1:8000/x"
    )

    assert motivo is not None, (
        "REST_FILE shim NON deve permettere loopback IPv4 anche con "
        "env opt-in settata. Pattern strict-no-opt-in violato."
    )
    # Il messaggio dalla primitive cita un IP privato/interno —
    # qualunque sia il fraseggio, deve essere non-vuoto e rifiutare.
    assert "privato/interno" in motivo or "loopback" in motivo.lower(), (
        f"motivo inatteso: {motivo!r}"
    )


def test_rest_file_shim_blocks_ipv6_loopback_even_with_env_opt_in(monkeypatch):
    """REST_FILE strict per IPv6 ::1 con env opt-in settata."""
    from app.core.arricchisci.api_connection_source_loading import (
        ApiConnectionSourceLoadingMixin,
    )

    monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "1")
    monkeypatch.setenv("ENVIRONMENT", "development")

    motivo = ApiConnectionSourceLoadingMixin._validate_outbound_url(
        "http://[::1]:8000/x"
    )

    assert motivo is not None, (
        "REST_FILE shim NON deve permettere loopback IPv6 (::1) anche "
        "con env opt-in settata."
    )


def test_validate_external_url_still_honors_loopback_opt_in_in_dev(monkeypatch):
    """Guard test contro regressione del wrapper raise pubblico.

    Il refactor PR-#35 NON deve cambiare il behavior di
    `validate_external_url` (raise wrapper): in dev, con env opt-in
    settata, loopback DEVE essere permesso (non solleva).

    Senza questo guard, un futuro refactor potrebbe accidentalmente
    forzare strict anche sul wrapper raise, rompendo i test funzionali
    B-2 che dipendono dall'opt-in via 127.0.0.1."""
    from app.shared.api_utils import validate_external_url

    monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "1")
    monkeypatch.setenv("ENVIRONMENT", "development")

    # In dev + opt-in: loopback deve essere accettato (no raise)
    validate_external_url("http://127.0.0.1:8000/x")  # se solleva → fail
