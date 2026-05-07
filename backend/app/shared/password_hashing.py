"""
Password hashing helpers — Argon2id (default) + bcrypt (legacy verify

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

from __future__ import annotations

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import (
    HashingError,  # noqa: F401 (compatibility export potenziale; mantenuto per parita'
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)


_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    if not isinstance(hashed, str) or not hashed:
        return False
    if hashed.startswith("$argon2"):
        try:
            return _ph.verify(hashed, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        except Exception:
            # Difensivo: argon2-cffi ha sollevato eccezioni non documentate
            # su input corrotti in passato. Fail-closed, no leak
            return False
    if hashed.startswith("$2"):
        try:
            return bcrypt.checkpw(password.encode(), hashed.encode())
        except (ValueError, TypeError):
            return False
    return False


def needs_rehash(hashed: str) -> bool:
    """True se l'hash va rigenerato in Argon2id al prossimo login riuscito.

    - bcrypt ($2*) -> True (migrazione)
    - Argon2id valido -> delega a PasswordHasher.check_needs_rehash
    - altro / corrotto -> False (niente rehash alla cieca, il verify
                                 avra' gia' ritornato False a monte)
    """
    if not isinstance(hashed, str) or not hashed:
        return False
    if hashed.startswith("$2"):
        return True
    if hashed.startswith("$argon2"):
        try:
            return _ph.check_needs_rehash(hashed)
        except InvalidHashError:
            return False
        except Exception:
            return False
    return False


# C10: hash dummy reale di una password casuale, generato una volta all'import
# Usato nel login quando l'username non esiste, cosi' `verify_password` fa
# comunque il lavoro di hashing e il tempo di risposta non rivela se un utente
# esiste o no (timing attack / user enumeration). Dall'introduzione di
# Argon2id il dummy e' in Argon2id: coerente con i nuovi utenti. Gli utenti
# NOTE: legacy bcrypt hanno un costo di verify leggermente diverso finche' non
# vengono migrati dal rehash progressivo
_DUMMY_PASSWORD_HASH = _ph.hash("c10_dummy_password_for_timing_parity")
