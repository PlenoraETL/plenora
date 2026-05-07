"""
Tests the shared crypto behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

import pytest

from app.shared import crypto


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def test_encrypt_is_idempotent():
    encrypted = crypto.encrypt("secret-123")

    assert crypto.encrypt(encrypted) == encrypted


def test_decrypt_keeps_legacy_plaintext_unchanged():
    assert crypto.decrypt("legacy-plaintext") == "legacy-plaintext"


def test_encrypt_dict_fields_and_decrypt_dict_fields_roundtrip():
    payload = {
        "password": "pwd-123",
        "client_secret": "secret-123",
        "name": "plain",
    }

    crypto.encrypt_dict_fields(payload, ["password", "client_secret"])

    assert crypto.is_encrypted(payload["password"])
    assert crypto.is_encrypted(payload["client_secret"])
    assert payload["name"] == "plain"

    crypto.decrypt_dict_fields(payload, ["password", "client_secret"])

    assert payload["password"] == "pwd-123"
    assert payload["client_secret"] == "secret-123"


def test_encrypt_raises_on_fernet_failure_never_returns_plaintext(monkeypatch):
    """S1: su errore di cifratura encrypt() deve fail-fast, MAI restituire plaintext.

    Regressione: in passato un except Exception silenzioso ritornava il valore
    originale in chiaro, col rischio di scrivere password/secret in plaintext
    nel DB se l'inizializzazione Fernet falliva a runtime.
    """

    class _BrokenFernet:
        def encrypt(self, _data):
            raise RuntimeError("simulated fernet failure")

    monkeypatch.setattr(crypto, "_get_fernet", lambda: _BrokenFernet())

    with pytest.raises(RuntimeError):
        crypto.encrypt("super-secret-password")
