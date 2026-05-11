"""
Module-level helpers per `ApiConnectionService`: crypto auth fields +

node-data accessor.
"""

from __future__ import annotations

from typing import Dict

from app.infrastructure.security.crypto import (
    CredentialUnavailableError,
    decrypt as _crypto_decrypt,
    encrypt as _crypto_encrypt,
    is_encrypted as _crypto_is_encrypted,
)


# Campi sensibili nel blocco auth di una API connection — vengono cifrati
# at-rest. Restano in chiaro in memoria durante l'uso e vengono re-cifrati
# prima del save nel DB
_AUTH_SENSITIVE_FIELDS = (
    "password",
    "client_secret",
    "token",
    "access_token",
    "refresh_token",
    "key_value",
    "value",
)


def _encrypt_auth_in_config(config: Dict) -> Dict:
    """Cifra in-place i campi sensibili di config['auth']. Idempotente."""
    auth = config.get("auth")
    if not isinstance(auth, dict):
        return config
    for field in _AUTH_SENSITIVE_FIELDS:
        val = auth.get(field)
        if isinstance(val, str) and val:
            auth[field] = _crypto_encrypt(val)
    return config


def _decrypt_auth_in_config(config: Dict, *, strict: bool = False) -> Dict:
    """Decifra in-place i campi sensibili di config['auth']. Idempotente.

    Se ``strict=True``, un campo con prefisso ``enc:`` il cui decrypt ritorna
    None solleva ``CredentialUnavailableError`` invece di silenziosamente
    valorizzare il campo a None. Usato dal path test_api_connection (Bug #2,
    2026-04-23) per non inviare chiamate HTTP con Authorization: "Bearer None".
    """
    auth = config.get("auth")
    if not isinstance(auth, dict):
        return config
    for field in _AUTH_SENSITIVE_FIELDS:
        val = auth.get(field)
        if isinstance(val, str) and val:
            was_encrypted = _crypto_is_encrypted(val)
            decrypted = _crypto_decrypt(val)
            if strict and was_encrypted and decrypted is None:
                raise CredentialUnavailableError(
                    f"api connection: campo auth.{field} non decriptabile "
                    "(chiave mancante/sbagliata o token corrotto)"
                )
            auth[field] = decrypted
    return config


def _nd(node: dict, key: str, default=None):
    """Legge un campo flat dal nodo dict, con fallback su node['data'] (backward compat)."""
    val = node.get(key)
    if val is not None:
        return val
    return node.get('data', {}).get(key, default) if isinstance(node.get('data'), dict) else default
