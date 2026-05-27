"""
Smoke contract — extracted boundary: estrazione password hashing.

Verifica due invarianti minime del refactor:
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def test_password_hashing_module_exposes_expected_symbols():
    from app.core.security import password_hashing as ph

    assert callable(ph.hash_password)
    assert callable(ph.verify_password)
    assert callable(ph.needs_rehash)
    assert isinstance(ph._DUMMY_PASSWORD_HASH, str)
    assert ph._DUMMY_PASSWORD_HASH.startswith("$argon2"), (
        "Il dummy hash deve essere Argon2id per timing-parity coerente "
        "con i nuovi utenti (vedi commento C10)."
    )
    # _ph e' una PasswordHasher singleton; verifichiamo che abbia il
    # metodo principale per evitare di legarci all'esatta classe.
    assert hasattr(ph._ph, "hash")
    assert hasattr(ph._ph, "verify")


def test_auth_reexports_password_hashing_with_object_identity():
    """auth.py re-esporta gli stessi oggetti: monkeypatch su auth_mod
    continua a funzionare; il modulo sorgente resta intatto."""
    from app.core.security import password_hashing as ph
    from app.core.auth import auth as auth_mod

    assert auth_mod.hash_password is ph.hash_password
    assert auth_mod.verify_password is ph.verify_password
    assert auth_mod.needs_rehash is ph.needs_rehash
    assert auth_mod._DUMMY_PASSWORD_HASH is ph._DUMMY_PASSWORD_HASH
    assert auth_mod._ph is ph._ph
