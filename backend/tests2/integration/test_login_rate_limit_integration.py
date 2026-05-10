"""
Integration test — rate-limit login per-IP via X-Forwarded-For.

A differenza del test unit (`tests2/security/unit/test_login_rate_limit_per_ip.py`
"""

from __future__ import annotations

import pytest

from tests2.functional.harness.http import http_json_response


pytestmark = [pytest.mark.integration, pytest.mark.security]


def _login_fail(base_url: str, xff_ip: str):
    """POST /api/auth/login con credenziali intenzionalmente wrong.
    Ritorna (status_code, body)."""
    status, body, _ = http_json_response(
        f"{base_url}/api/auth/login",
        method="POST",
        payload={"username": "nobody-rate-limit-test", "password": "wrong"},
        headers={"X-Forwarded-For": xff_ip},
        timeout=10.0,
    )
    return status, body


def test_rate_limit_triggers_after_max_attempts_per_ip(integration_backend_server):
    """Ogni attempt con stesso XFF fa salire il counter del bucket IP
    risolto. Dopo LOGIN_MAX_ATTEMPTS (default 10) il successivo riceve 429.
    """
    base_url = integration_backend_server["base_url"]
    xff = "10.0.0.101"

    # Spam fino a trovare il primo 429. Usiamo cap alto cosi' il test
    # sopravvive a LOGIN_MAX_ATTEMPTS customizzato nel deploy.
    saw_429 = False
    for i in range(1, 25):
        status, _body = _login_fail(base_url, xff)
        if status == 429:
            saw_429 = True
            break
        assert status in (401, 403, 422), (
            f"attempt #{i}: atteso 401/403/422 prima del rate-limit, got {status}"
        )

    assert saw_429, (
        "Mai ricevuto 429 dopo 24 failed login dallo stesso XFF IP — "
        "il rate-limit e' disabilitato o la risoluzione client IP e' rotta"
    )


def test_rate_limit_isolated_per_forwarded_ip(integration_backend_server):
    """Bucket separato per XFF IP. Se saturano "A", "B" resta libero.

    Regressione diretta del reperto #27: Previous behavior, TUTTI i peer diventavano
    `127.0.0.1` (peer nginx/uvicorn locale) e condividevano il bucket.
    """
    base_url = integration_backend_server["base_url"]
    xff_a = "10.0.0.201"
    xff_b = "10.0.0.202"

    # Satura il bucket di A finche' non triggera 429
    for i in range(1, 25):
        status, _ = _login_fail(base_url, xff_a)
        if status == 429:
            break
    else:
        pytest.fail("A non ha mai triggerato 429 — rate-limit disabilitato?")

    # B deve essere ancora libero: primo tentativo NON deve essere 429.
    # Accettiamo 401 (credenziali wrong) o 403 (utente disabilitato) —
    # ma NON 429 che indicherebbe bucket condiviso.
    status_b, _ = _login_fail(base_url, xff_b)
    assert status_b != 429, (
        f"XFF B ({xff_b}) ha ricevuto 429 nonostante nessun suo attempt "
        f"precedente — il bucket e' condiviso (regressione pre-#27 fix)"
    )
    assert status_b in (401, 403, 422)
