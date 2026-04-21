"""Contract test per `app.shared.crypto._get_raw_key` — key sourcing.

Invariants blindati (da ``backend/docs/architecture/invariants.md``):

- §1.5 "Config loading" + coda §1.5 riga "env var crypto-key": la chain
  di priorità è ``PLENORA_ENCRYPTION_KEY`` → ``URBY_ENCRYPTION_KEY``
  (alias retro-compat) → ``CONN_ENCRYPT_KEY`` (legacy). Ogni nuovo alias
  deve passare da qui con PR dedicata.
- §3 riga ``ENVIRONMENT``: in produzione niente chiave → fail-fast
  ``RuntimeError``; in produzione chiave uguale a ``_DEV_DEFAULT_KEY`` →
  fail-fast; chiave più corta di ``_MIN_KEY_LENGTH`` → fail-fast in
  qualunque ambiente.
- §3 riga ``PLENORA_DISABLE_DEFAULT_KEY``: dev opt-in che replica
  fail-fast prod.
- §6 D5 chiusa 2026-04-20: il fallback su ``SECRET_KEY`` e
  ``JWT_SECRET_KEY`` è stato RIMOSSO dalla chain. Questo test blinda
  la regressione: un merge futuro che riesuma il fallback deve rompere
  qui.

Regressioni protette:

- Riesumazione silenziosa del fallback ``SECRET_KEY`` / ``JWT_SECRET_KEY``
  (D5): se qualcuno riaggiunge quei nomi alla chain, un ``SECRET_KEY``
  settato per JWT verrebbe usato anche come crypto key → rotazione JWT
  romperebbe silent la decifratura dei record. Test esplicito che
  quei nomi NON vengono più letti.
- Alias chain priority: PLENORA deve vincere su URBY, URBY su CONN. Una
  regressione (ordine invertito) causerebbe decrypt fallito in deploy
  che hanno entrambe le env var settate durante il rollout.
"""

from __future__ import annotations

import pytest

from app.shared import crypto


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# Chiavi sintetiche per i test. Devono essere ≥ _MIN_KEY_LENGTH (16 char).
_KEY_PLENORA = "plenora-key-padding-32chars-xxxx"
_KEY_URBY    = "urby-key-padding-32chars-xxxxxxxx"
_KEY_CONN    = "conn-key-padding-32chars-xxxxxxxx"


@pytest.fixture(autouse=True)
def _reset_fernet_cache_and_clean_env(monkeypatch):
    """Pulisce cache + tutte le env var crypto rilevanti prima di ogni test.

    Necessario per deterministicità: il test runner può ereditare da shell
    chiavi valorizzate, e la cache Fernet è globale.
    """
    for name in (
        "PLENORA_ENCRYPTION_KEY",
        "URBY_ENCRYPTION_KEY",
        "CONN_ENCRYPT_KEY",
        "SECRET_KEY",
        "JWT_SECRET_KEY",
        "PLENORA_DISABLE_DEFAULT_KEY",
        "ENVIRONMENT",
        "ENV",
    ):
        monkeypatch.delenv(name, raising=False)
    crypto._reset_cache_for_tests()
    yield
    crypto._reset_cache_for_tests()


# ─── 1) Chain priority ────────────────────────────────────────────────────────

def test_key_chain_plenora_wins_over_urby_and_conn(monkeypatch):
    """Blinda §1.5: PLENORA_ENCRYPTION_KEY vince sulla chain.

    Regressione: se qualcuno inverte l'ordine del for-loop in
    ``_get_raw_key``, deploy con entrambe le var settate (situazione
    intermedia del rollout D4) decifrano con la chiave sbagliata e
    il ciphertext diventa inaccessibile silently.
    """
    monkeypatch.setenv("PLENORA_ENCRYPTION_KEY", _KEY_PLENORA)
    monkeypatch.setenv("URBY_ENCRYPTION_KEY", _KEY_URBY)
    monkeypatch.setenv("CONN_ENCRYPT_KEY", _KEY_CONN)

    assert crypto._get_raw_key() == _KEY_PLENORA


def test_key_chain_urby_wins_when_plenora_absent(monkeypatch):
    """URBY_ENCRYPTION_KEY è il secondo in priorità (alias retro-compat)."""
    monkeypatch.setenv("URBY_ENCRYPTION_KEY", _KEY_URBY)
    monkeypatch.setenv("CONN_ENCRYPT_KEY", _KEY_CONN)

    assert crypto._get_raw_key() == _KEY_URBY


def test_key_chain_conn_used_only_when_others_absent(monkeypatch):
    """CONN_ENCRYPT_KEY è il fallback legacy finale della chain."""
    monkeypatch.setenv("CONN_ENCRYPT_KEY", _KEY_CONN)

    assert crypto._get_raw_key() == _KEY_CONN


# ─── 2) D5 regression — niente fallback su SECRET_KEY / JWT_SECRET_KEY ────────

def test_d5_no_fallback_on_secret_key_or_jwt_secret_key(monkeypatch):
    """Blinda §6 D5 (chiusa 2026-04-20): la chain NON fallbacka più su
    ``SECRET_KEY`` / ``JWT_SECRET_KEY``. Quei nomi appartengono al
    dominio auth JWT e condividerli renderebbe la rotazione JWT
    un'operazione silently distruttiva sui record cifrati.

    Regressione: un merge futuro che riaggiunge quei nomi alla chain
    deve rompere qui. In dev (ENV non-prod), senza chiavi canoniche,
    il comportamento corretto è ricadere sulla ``_DEV_DEFAULT_KEY``
    con warning, NON leggere SECRET_KEY.
    """
    # Settiamo solo i nomi JWT-only: la chain NON deve leggerli.
    monkeypatch.setenv("SECRET_KEY", "jwt-secret-NEVER-use-as-crypto-key")
    monkeypatch.setenv("JWT_SECRET_KEY", "jwt-other-NEVER-use-as-crypto-key")
    # Assicuriamoci di essere in dev (default, ma esplicitiamo).
    monkeypatch.setenv("ENVIRONMENT", "development")

    result = crypto._get_raw_key()

    assert result == crypto._DEV_DEFAULT_KEY, (
        f"Con solo SECRET_KEY/JWT_SECRET_KEY settate in dev, la chain "
        f"DEVE ricadere sulla dev default key, NON usare i nomi JWT. "
        f"Ottenuto: {result!r}"
    )
    # Doppio check: il valore restituito NON deve essere uno dei due segreti JWT.
    assert "jwt-secret" not in result, (
        "Regressione D5: SECRET_KEY è stato usato come crypto key"
    )
    assert "jwt-other" not in result, (
        "Regressione D5: JWT_SECRET_KEY è stato usato come crypto key"
    )


# ─── 3) Fail-fast in produzione ───────────────────────────────────────────────

def test_fail_fast_in_production_when_no_key_configured(monkeypatch):
    """Blinda §3 riga ``ENVIRONMENT``: in prod niente chiave → RuntimeError.

    Regressione: in passato l'app avrebbe potuto bootare silently in
    prod con la dev default key. Il fail-fast è la safety-net.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="chiave di cifratura"):
        crypto._get_raw_key()


def test_fail_fast_when_dev_default_key_leaked_to_production(monkeypatch):
    """Blinda hardening Block F-D: se qualcuno copia il dev default key
    in prod, trattiamo come "nessuna chiave configurata" → fail-fast.

    Scenario reale: copia-incolla di ``.env.example`` su server prod
    senza regenerare la chiave. Senza questa guardia l'attaccante che
    conosce il sorgente open potrebbe cifrare/decifrare.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PLENORA_ENCRYPTION_KEY", crypto._DEV_DEFAULT_KEY)

    with pytest.raises(RuntimeError, match="default di sviluppo"):
        crypto._get_raw_key()


def test_fail_fast_when_key_shorter_than_minimum_in_any_environment(monkeypatch):
    """Blinda hardening: chiave < ``_MIN_KEY_LENGTH`` → RuntimeError
    anche in dev. Probabile errore di configurazione.

    Scelta intenzionale: a differenza del dev default leak (che è un
    warning in dev), questo è fail-fast in qualunque ambiente perché
    una chiave troppo corta non è mai un uso legittimo.
    """
    short_key = "x" * (crypto._MIN_KEY_LENGTH - 1)  # 15 char: sotto il minimo
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("PLENORA_ENCRYPTION_KEY", short_key)

    with pytest.raises(RuntimeError, match="troppo corta"):
        crypto._get_raw_key()


# ─── 4) Opt-in dev fail-fast replica ──────────────────────────────────────────

def test_disable_default_key_opt_in_triggers_fail_fast_in_dev(monkeypatch):
    """Blinda §3 riga ``PLENORA_DISABLE_DEFAULT_KEY``: opt-in dev che
    replica il fail-fast prod. Utile per developer che vogliono
    riprodurre il comportamento prod localmente.

    Regressione: se il check dell'opt-in viene rimosso, i developer
    che testano comportamenti prod-like non scoprono più
    misconfigurazioni prima del deploy.
    """
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("PLENORA_DISABLE_DEFAULT_KEY", "1")
    # Nessuna chiave nelle canoniche

    with pytest.raises(RuntimeError, match="PLENORA_DISABLE_DEFAULT_KEY"):
        crypto._get_raw_key()
