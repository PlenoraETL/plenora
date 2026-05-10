"""
Contract test per `app.infrastructure.security.crypto` — livello primitive Fernet.

Invariants blindati (da ``backend/docs/architecture/invariants.md``):
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from app.infrastructure.security import crypto


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _reset_fernet_cache():
    """Garantisce che ogni test parta con la cache Fernet pulita.

    Necessario perché ``_get_fernet`` cache-a globalmente la chiave
    derivata; senza reset un test che sostituisce la key tramite env var
    verrebbe inquinato dalla cache di un test precedente.
    """
    crypto._reset_cache_for_tests()
    yield
    crypto._reset_cache_for_tests()


def test_encrypt_decrypt_roundtrip_preserves_plaintext():
    """Blinda il contratto canonico: decrypt(encrypt(x)) == x.

    Copertura esplicita della proprietà roundtrip end-to-end, che il
    test legacy ``test_shared_crypto_contract`` esercita solo
    indirettamente via ``encrypt_dict_fields``. Duplice garanzia:
    (a) formato ``enc:<base64token>`` riconosciuto da ``is_encrypted``,
    (b) plaintext originale ricostruito byte-identico.
    """
    plaintext = "pa$$w0rd con spazi, UTF-8 àèé, e simboli #!?"

    ciphertext = crypto.encrypt(plaintext)

    assert ciphertext is not None
    assert ciphertext.startswith(crypto.ENC_PREFIX), (
        f"ciphertext privo di prefisso enc:, ottenuto {ciphertext[:20]!r}"
    )
    assert crypto.is_encrypted(ciphertext) is True
    assert crypto.decrypt(ciphertext) == plaintext


def test_decrypt_tampered_ciphertext_returns_none_and_logs_error(monkeypatch):
    """Blinda §2 riga decrypt: bit flip nel ciphertext → None, log error.

    Il MAC HMAC-SHA256 di Fernet fallisce la verifica → ``InvalidToken``
    intercettato nel modulo → ritorno ``None`` (mai plaintext, mai raise
    scoperto). Il logger registra un ``error`` con causa esplicita, così
    la telemetria osserva il tamper-event invece di un silent drop.

    Nota test harness: il ``StructuredLogger`` dell'app ha
    ``propagate=False``, quindi ``caplog`` non intercetta. Spiamo
    direttamente ``crypto.logger.error`` con MagicMock — più esplicito
    e disaccoppiato dalla config handler.
    """
    original = "super-secret-value-01"
    ciphertext = crypto.encrypt(original)
    assert ciphertext is not None and ciphertext.startswith(crypto.ENC_PREFIX)

    token = ciphertext[len(crypto.ENC_PREFIX):]
    raw = base64.urlsafe_b64decode(token.encode("utf-8"))
    # Alteriamo l'ultimo byte (parte della MAC HMAC-SHA256 di Fernet).
    tampered_raw = raw[:-1] + bytes([raw[-1] ^ 0xFF])
    tampered_token = base64.urlsafe_b64encode(tampered_raw).decode("utf-8")
    tampered = f"{crypto.ENC_PREFIX}{tampered_token}"

    spy_error = MagicMock()
    monkeypatch.setattr(crypto.logger, "error", spy_error)

    result = crypto.decrypt(tampered)

    assert result is None, (
        f"decrypt su token manomesso DEVE ritornare None, ottenuto {result!r}"
    )
    assert spy_error.called, "crypto.decrypt DEVE loggare error su tamper"
    logged_msg = " ".join(str(a) for a in spy_error.call_args.args)
    assert "decrypt" in logged_msg.lower() or "fernet" in logged_msg.lower(), (
        f"atteso log error crypto.decrypt/fernet, ottenuto: {logged_msg!r}"
    )


def test_decrypt_with_different_key_returns_none(monkeypatch):
    """Blinda la semantica di fallback: chiave sbagliata → None, mai raise.

    Scenario canonico di key rotation parziale: un record è stato cifrato
    con la chiave A, ma l'app ora gira con la chiave B (es. rotate non
    propagato, misconfigurazione). Il contratto §2 garantisce che la
    connessione fallirà al primo uso con errore visibile, ma l'app
    non crasha globalmente.
    """
    # Encrypt con chiave A
    monkeypatch.setenv("PLENORA_ENCRYPTION_KEY", "key-A" + "X" * 20)
    # Cancella override da env sibling che può vincere il lookup
    monkeypatch.delenv("URBY_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("CONN_ENCRYPT_KEY", raising=False)
    crypto._reset_cache_for_tests()
    ciphertext_under_A = crypto.encrypt("secret-under-A")
    assert ciphertext_under_A is not None

    # Ora ruotiamo alla chiave B, simulando rotate senza ri-cifrare i dati
    monkeypatch.setenv("PLENORA_ENCRYPTION_KEY", "key-B" + "Y" * 20)
    crypto._reset_cache_for_tests()

    result = crypto.decrypt(ciphertext_under_A)

    assert result is None, (
        f"decrypt con chiave diversa DEVE ritornare None, ottenuto {result!r}"
    )


def test_encrypt_dict_fields_skips_missing_non_string_and_non_listed():
    """Blinda la disciplina di ``encrypt_dict_fields``:
    - campi fuori dalla lista → restano plaintext (allowlist)
    - campi assenti → no-op silenzioso (non KeyError)
    - campi con valore non-string (None, int, dict) → no-op silenzioso

    Regressione: se la utility provasse a cifrare None o non-string,
    romperebbe i flussi save su record parzialmente popolati (host noto,
    password nulla ancora — è uno stato valido nel nostro dominio).
    """
    payload = {
        "password": "pwd-123",
        "api_token": "tok-456",
        "host": "db.internal",       # fuori allowlist, resta plaintext
        "port": 5432,                # non-string, resta com'è
        "client_secret": None,       # non-string (None), resta None
        "meta": {"nested": "obj"},   # non-string (dict), resta com'è
        # "missing_field" non è nel dict: non DEVE essere aggiunto
    }

    crypto.encrypt_dict_fields(
        payload,
        ["password", "api_token", "client_secret", "missing_field"],
    )

    assert crypto.is_encrypted(payload["password"]), "password DEVE essere cifrata"
    assert crypto.is_encrypted(payload["api_token"]), "api_token DEVE essere cifrato"
    assert payload["host"] == "db.internal", "host fuori allowlist DEVE restare plaintext"
    assert payload["port"] == 5432, "port non-string DEVE restare com'è"
    assert payload["client_secret"] is None, "None DEVE essere passato through"
    assert payload["meta"] == {"nested": "obj"}, "dict non-string DEVE restare com'è"
    assert "missing_field" not in payload, (
        "missing_field non presente DEVE rimanere assente (no side-effect add)"
    )


def test_encrypt_same_plaintext_twice_produces_different_ciphertext():
    """Blinda la proprietà IV-random di Fernet: due encrypt dello stesso
    plaintext con la stessa chiave producono ciphertext distinti.

    Regressione: se per qualsiasi motivo futuro si sostituisse Fernet
    con un wrapper deterministico (es. "cache aggressive" mal-pensata),
    pattern di plaintext uguali diventerebbero inferibili dal DB dump —
    crepa di sicurezza grave. Questo test guarda il contract di Fernet
    tramite la superficie pubblica del nostro modulo.

    Entrambi i ciphertext devono comunque decifrare al plaintext originale.
    """
    plaintext = "stable-value-to-reencrypt"

    # is_encrypted fa idempotenza → dobbiamo partire da plaintext puro.
    # Ogni chiamata deve generare un IV fresh.
    ct1 = crypto.encrypt(plaintext)
    ct2 = crypto.encrypt(plaintext)

    assert ct1 is not None and ct2 is not None
    assert ct1 != ct2, (
        "encrypt deterministico è un rischio di sicurezza: i due ciphertext "
        "DEVONO differire per IV (nonce) casuale di Fernet"
    )
    assert crypto.decrypt(ct1) == plaintext
    assert crypto.decrypt(ct2) == plaintext
