"""
Contract test per `app.infrastructure.security.crypto._get_raw_key` — key sourcing.

Invariants blindati (da ``backend/docs/architecture/invariants.md``):
"""

from __future__ import annotations

import pytest

from app.infrastructure.security import crypto


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


def test_key_chain_plenora_is_used_and_legacy_aliases_are_ignored(monkeypatch):
    """Blinda §1.5 + §6 D4: la chain legge SOLO PLENORA_ENCRYPTION_KEY.

    Settiamo intenzionalmente anche gli alias legacy URBY_ENCRYPTION_KEY
    e CONN_ENCRYPT_KEY con valori DIVERSI dal canonical. Se qualcuno
    riaggiunge gli alias alla chain e la legge PRIMA di PLENORA (ordine
    invertito), il test rileva la regressione perche' `_get_raw_key()`
    restituirebbe un valore != `_KEY_PLENORA`.
    """
    monkeypatch.setenv("PLENORA_ENCRYPTION_KEY", _KEY_PLENORA)
    monkeypatch.setenv("URBY_ENCRYPTION_KEY", _KEY_URBY)
    monkeypatch.setenv("CONN_ENCRYPT_KEY", _KEY_CONN)

    assert crypto._get_raw_key() == _KEY_PLENORA


def test_urby_encryption_key_tombstoned_alone(monkeypatch):
    """Blinda §6 D4 (chiuso 2026-04-23): URBY_ENCRYPTION_KEY NON e' piu'
    letta dalla chain. Settata da sola in dev, il comportamento corretto
    e' ricadere sulla `_DEV_DEFAULT_KEY` (con warning rumoroso), NON usare
    URBY_ENCRYPTION_KEY.

    Regressione: un merge futuro che riaggiunge l'alias alla chain
    farebbe questo test passare `_KEY_URBY` invece di `_DEV_DEFAULT_KEY`
    → test rompe. Deploy con env legacy nel ``.env`` avrebbero chiave
    diversa da quella canonical attesa.
    """
    monkeypatch.setenv("URBY_ENCRYPTION_KEY", _KEY_URBY)
    monkeypatch.setenv("ENVIRONMENT", "development")

    result = crypto._get_raw_key()

    assert result == crypto._DEV_DEFAULT_KEY, (
        f"URBY_ENCRYPTION_KEY e' tombstoned (D4 chiuso): settarla da sola "
        f"in dev DEVE ricadere sulla dev default key, NON usare il valore. "
        f"Ottenuto: {result!r}"
    )


def test_urby_encryption_key_tombstoned_fails_fast_in_prod(monkeypatch):
    """Blinda §6 D4 + §3 ENVIRONMENT: con URBY_ENCRYPTION_KEY settata da
    sola in production, il sistema deve fail-fast (chain non la legge,
    nessuna chiave canonical configurata).
    """
    monkeypatch.setenv("URBY_ENCRYPTION_KEY", _KEY_URBY)
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="chiave di cifratura"):
        crypto._get_raw_key()


def test_conn_encrypt_key_tombstoned_alone(monkeypatch):
    """Blinda §6 D4: CONN_ENCRYPT_KEY NON e' piu' letta dalla chain.
    Settata da sola in dev, ricade su dev default key.
    """
    monkeypatch.setenv("CONN_ENCRYPT_KEY", _KEY_CONN)
    monkeypatch.setenv("ENVIRONMENT", "development")

    result = crypto._get_raw_key()

    assert result == crypto._DEV_DEFAULT_KEY, (
        f"CONN_ENCRYPT_KEY e' tombstoned (D4 chiuso): settarla da sola "
        f"in dev DEVE ricadere sulla dev default key, NON usare il valore. "
        f"Ottenuto: {result!r}"
    )


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


@pytest.mark.parametrize("env_name", ["staging", "stage", "STAGING", "Stage"])
def test_fail_fast_in_staging_when_no_key_configured(env_name, monkeypatch):
    """Blinda S1 / P2.5 di runtime_config_audit.md (chiuso 2026-04-23,
    B6/#5): il guard fail-fast copre anche `staging` / `stage`, simmetrico
    a `config.py:26 _DOTENV_DISABLED_ENVS`.

    Regressione: prima del fix il tuple era `("production", "prod")` e
    un deploy con `ENVIRONMENT=staging` senza chiave cadeva silently
    su `_DEV_DEFAULT_KEY` con solo warning. In staging (ambiente non
    zero-trust ma non isolato) questo e' un rischio medio: record
    cifrati con chiave pubblica nota.
    """
    monkeypatch.setenv("ENVIRONMENT", env_name)

    with pytest.raises(RuntimeError, match="chiave di cifratura"):
        crypto._get_raw_key()


@pytest.mark.parametrize("env_name", ["staging", "stage"])
def test_fail_fast_when_dev_default_key_leaked_to_staging(env_name, monkeypatch):
    """Blinda S1 / P2.5: il leak del dev-default in staging e' fail-fast,
    identicamente a production. Senza questa guardia lo stesso scenario
    di copy-paste `.env.example` su server staging bypassa il check.
    """
    monkeypatch.setenv("ENVIRONMENT", env_name)
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
