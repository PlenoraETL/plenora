"""Lock tests per `app.shared.api_utils.validate_external_url`.

Garantisce il contratto SSRF anti-loopback + l'opt-in di test
`PLENORA_ALLOW_LOOPBACK_OUTBOUND=1` (canonical) / `URBY_ALLOW_LOOPBACK_OUTBOUND=1`
(alias retro-compat, deprecato — rinominato 2026-04-23 / P1.5). Senza
questi test, una regression silente nella validazione SSRF abiliterebbe
richieste backend verso servizi bind-loopback (Redis, Postgres, EC2
metadata, ecc.).

Blinda anche S6 / P2.4 (chiuso 2026-04-23): il flag e' IGNORATO by
design in ambienti hardened (`ENVIRONMENT in {production, prod,
staging, stage}`), simmetrico a `crypto.py` guard e
`config.py _DOTENV_DISABLED_ENVS`.
"""

import pytest

from app.shared.api_utils import validate_external_url


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


@pytest.fixture(autouse=True)
def _clean_loopback_env(monkeypatch):
    """Pulisce env loopback + ENVIRONMENT prima di ogni test: deterministico."""
    for name in (
        "PLENORA_ALLOW_LOOPBACK_OUTBOUND",
        "URBY_ALLOW_LOOPBACK_OUTBOUND",
        "ENVIRONMENT",
        "ENV",
    ):
        monkeypatch.delenv(name, raising=False)


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


def test_rejects_loopback_by_default():
    """Senza `PLENORA_ALLOW_LOOPBACK_OUTBOUND=1` il loopback e' bloccato
    per defense-in-depth (Redis, Postgres locali bind 127.0.0.1)."""
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://127.0.0.1:6379/")


# =============================================================================
# Test opt-in: PLENORA_ALLOW_LOOPBACK_OUTBOUND (canonical)
# =============================================================================


def test_loopback_allowed_when_opt_in_env_is_set(monkeypatch):
    """Con `PLENORA_ALLOW_LOOPBACK_OUTBOUND=1` il loopback passa il check.
    Pensata per i test funzionali che usano un fake server localhost."""
    monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "1")
    # Non solleva
    validate_external_url("http://127.0.0.1:51360/json/ok")


def test_opt_in_does_not_unlock_other_private_ranges(monkeypatch):
    """L'opt-in apre SOLO loopback. 10.x / 192.168.x / 172.16.x restano
    bloccati anche con la env var settata: il blast radius dell'opt-in
    e' minimo."""
    monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "1")
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
        monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", bad)
        if bad.strip() == "1":
            # Caso edge: " 1 " strip-ato e' "1", quindi passa
            continue
        with pytest.raises(ValueError, match="URL bloccato"):
            validate_external_url("http://127.0.0.1/")


# =============================================================================
# Retro-compat alias + guard hardened env (P1.5 + S6/P2.4, 2026-04-23)
# =============================================================================


def test_legacy_urby_alias_still_honored_in_dev(monkeypatch):
    """Blinda P1.5 (rinomina 2026-04-23): l'alias legacy
    `URBY_ALLOW_LOOPBACK_OUTBOUND` continua a funzionare in dev/test
    per retro-compat. Deploy o test-harness non ancora migrati
    devono continuare a operare.
    """
    monkeypatch.setenv("URBY_ALLOW_LOOPBACK_OUTBOUND", "1")
    # Non solleva — il fallback legacy attiva l'opt-in
    validate_external_url("http://127.0.0.1:51360/ok")


@pytest.mark.parametrize("hardened_env", ["production", "prod", "staging", "stage"])
@pytest.mark.parametrize("flag_name", [
    "PLENORA_ALLOW_LOOPBACK_OUTBOUND",
    "URBY_ALLOW_LOOPBACK_OUTBOUND",
])
def test_flag_ignored_in_hardened_environments(hardened_env, flag_name, monkeypatch):
    """Blinda S6 / P2.4 (chiuso 2026-04-23): in ambienti hardened
    (`ENVIRONMENT in {production, prod, staging, stage}`) il flag e'
    IGNORATO indipendentemente dal valore.

    Scenario reale: un worker prod con `PLENORA_ALLOW_LOOPBACK_OUTBOUND=1`
    settato per errore nella shell NON deve aprire SSRF verso bind-loopback
    (Redis/Postgres/EC2 metadata). Simmetrico a `crypto.py` guard.
    """
    monkeypatch.setenv("ENVIRONMENT", hardened_env)
    monkeypatch.setenv(flag_name, "1")

    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://127.0.0.1:6379/")
