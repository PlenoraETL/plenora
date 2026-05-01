"""Smoke contract — STRUCT-005 Seam A: estrazione password hashing.

Verifica due invarianti minime del refactor:

  1. Il nuovo modulo `app.shared.password_hashing` espone i 5 simboli
     attesi (`_ph`, `hash_password`, `verify_password`, `needs_rehash`,
     `_DUMMY_PASSWORD_HASH`).
  2. `app.core.auth.auth` re-esporta gli stessi oggetti (identita'
     bit-per-bit, non copie). Cosi' i test che fanno
     `monkeypatch.setattr(auth_mod, "verify_password", ...)` continuano
     a comportarsi come prima — modificano l'attributo del modulo
     `auth_mod`, mentre `app.shared.password_hashing.verify_password`
     resta intatto.

La copertura funzionale (Argon2 round-trip, bcrypt legacy, needs_rehash,
fail-closed su corrupt) e' gia' in `test_password_hashing_contract.py`.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def test_password_hashing_module_exposes_expected_symbols():
    from app.shared import password_hashing as ph

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
    from app.shared import password_hashing as ph
    from app.core.auth import auth as auth_mod

    assert auth_mod.hash_password is ph.hash_password
    assert auth_mod.verify_password is ph.verify_password
    assert auth_mod.needs_rehash is ph.needs_rehash
    assert auth_mod._DUMMY_PASSWORD_HASH is ph._DUMMY_PASSWORD_HASH
    assert auth_mod._ph is ph._ph
