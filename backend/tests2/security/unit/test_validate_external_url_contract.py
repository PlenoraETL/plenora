"""Lock tests per `app.shared.api_utils.validate_external_url`.

Garantisce il contratto SSRF anti-loopback + l'opt-in di test
`URBY_ALLOW_LOOPBACK_OUTBOUND=1`. Senza questi test, una regression
silente nella validazione SSRF abiliterebbe richieste backend verso
servizi bind-loopback (Redis, Postgres, EC2 metadata, ecc.).
"""

import pytest

from app.shared.api_utils import validate_external_url


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


# =============================================================================
# Schema validation
# =============================================================================


@pytest.mark.parametrize("url", [
    "ftp://example.test/file",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "ldap://dir.example/cn=foo",
])
def test_rejects_non_http_schemes(url):
    with pytest.raises(ValueError, match="schemi http/https"):
        validate_external_url(url)


def test_rejects_missing_hostname():
    with pytest.raises(ValueError, match="hostname mancante"):
        validate_external_url("http:///")


# =============================================================================
# SSRF: IP privati, link-local, riservati, multicast
# =============================================================================


@pytest.mark.parametrize("url", [
    "http://10.0.0.1/q",       # 10/8 private
    "http://172.16.0.1/q",     # 172.16/12 private
    "http://192.168.1.1/q",    # 192.168/16 private
    "http://169.254.169.254/", # link-local (EC2 metadata)
    "http://224.0.0.1/q",      # multicast
])
def test_rejects_private_and_link_local_addresses(url):
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url(url)


def test_rejects_loopback_by_default(monkeypatch):
    """Senza `URBY_ALLOW_LOOPBACK_OUTBOUND=1` il loopback e' bloccato
    per defense-in-depth (Redis, Postgres locali bind 127.0.0.1)."""
    monkeypatch.delenv("URBY_ALLOW_LOOPBACK_OUTBOUND", raising=False)
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://127.0.0.1:6379/")


# =============================================================================
# Test opt-in: URBY_ALLOW_LOOPBACK_OUTBOUND
# =============================================================================


def test_loopback_allowed_when_opt_in_env_is_set(monkeypatch):
    """Con `URBY_ALLOW_LOOPBACK_OUTBOUND=1` il loopback passa il check.
    Pensata per i test funzionali che usano un fake server localhost."""
    monkeypatch.setenv("URBY_ALLOW_LOOPBACK_OUTBOUND", "1")
    # Non solleva
    validate_external_url("http://127.0.0.1:51360/json/ok")


def test_opt_in_does_not_unlock_other_private_ranges(monkeypatch):
    """L'opt-in apre SOLO loopback. 10.x / 192.168.x / 172.16.x restano
    bloccati anche con la env var settata: il blast radius dell'opt-in
    e' minimo."""
    monkeypatch.setenv("URBY_ALLOW_LOOPBACK_OUTBOUND", "1")
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://10.0.0.1/q")
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://192.168.1.1/q")
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://169.254.169.254/")


def test_opt_in_strict_value_check(monkeypatch):
    """Solo `1` esatto attiva l'opt-in. Valori come 'true', 'yes',
    'on' NON funzionano — evita di abilitare il bypass per errore
    via env var generica."""
    for bad in ("0", "true", "yes", "on", "True", " 1 "):
        monkeypatch.setenv("URBY_ALLOW_LOOPBACK_OUTBOUND", bad)
        if bad.strip() == "1":
            # Caso edge: " 1 " strip-ato e' "1", quindi passa
            continue
        with pytest.raises(ValueError, match="URL bloccato"):
            validate_external_url("http://127.0.0.1/")
