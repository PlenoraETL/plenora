"""
Module-level helpers per `ApiConnectionService`: crypto auth fields +
node-data accessor.

Origine
=======
Estratto da `app.core.arricchisci.api_connector` come parte di Phase 5
Batch B3-B.1 (cluster split, vedi `orchestrate/PHASE_5_ENTRY_PLAN.md`
§3 P5-B3-B; pattern di estrazione di module-level utilities standalone,
simmetrico a B3-A.1 (`RateLimiter` standalone). NON e' un mixin: sono
funzioni module-level + 1 costante.

Razionale del raggruppamento: 3 helper module-level + 1 costante che
condividono il dominio "API connection auth utilities":

  - `_AUTH_SENSITIVE_FIELDS`        — costante tuple dei nomi dei campi
                                      sensibili dell'auth dict cifrati
                                      at-rest in `plenora.api_connections`.
  - `_encrypt_auth_in_config`       — cifra in-place i campi sensibili
                                      di `config['auth']` via `app.shared.crypto`.
                                      Idempotente.
  - `_decrypt_auth_in_config`       — decifra in-place. Idempotente.
                                      `strict=True` per Bug #2 (2026-04-23):
                                      campo `enc:` non decriptabile solleva
                                      `CredentialUnavailableError` invece
                                      di valorizzare a None silenziosamente
                                      (evita HTTP `Authorization: Bearer None`).
  - `_nd`                           — node-data accessor con fallback
                                      `node['data']` per backward compat
                                      vecchio canvas shape.

Cluster module-level (NON mixin) coeso, single-responsibility, riusabile
da qualunque cluster B3-B.x successivo via direct import.

Pattern: standalone module
==========================
NON e' un mixin (a differenza dei cluster B3-B.2+ che saranno mixin via
MRO eredita B3-A). Sono utility module-level pure (zero `self`).

`api_connector.py` re-exporta i 3 simboli + la costante per preservare
back-compat con i caller esterni che importano direttamente:

    from app.core.arricchisci.api_connector import _decrypt_auth_in_config  # tests
    from app.core.arricchisci.api_connector import _nd                       # tests

Re-export via:

    from app.core.arricchisci._api_connection_helpers import (
        _AUTH_SENSITIVE_FIELDS,
        _encrypt_auth_in_config,
        _decrypt_auth_in_config,
        _nd,
    )

Coupling esterno
================
- `app.shared.crypto.encrypt` (alias `_crypto_encrypt`) usato da
  `_encrypt_auth_in_config`.
- `app.shared.crypto.decrypt` (alias `_crypto_decrypt`) usato da
  `_decrypt_auth_in_config`.
- `app.shared.crypto.is_encrypted` (alias `_crypto_is_encrypted`) usato
  da `_decrypt_auth_in_config`.
- `app.shared.crypto.CredentialUnavailableError` raised da
  `_decrypt_auth_in_config` quando `strict=True` + decrypt fail.

Logger
======
**ZERO `logger.*` calls** in tutti e 3 gli helper (verificato body
pre-extraction). Quindi questo modulo NON definisce `logger`. Pattern
analogo a `_rate_limiter.py` (B3-A.1) e
`enrichment_validation_preview.py` / `enrichment_grouping.py`.

Coupling test contract
======================
Verificato pre-extraction (preflight B3-B.1):
  - 0 `inspect.getsource` o AST scan su questi simboli.
  - 0 `monkeypatch.setattr(<*>_module, "logger", ...)` riguardanti.
  - **Direct import contract** (back-compat):
    - `tests2/arricchisci/unit/test_api_connection_decrypt_policy.py`
      importa `_decrypt_auth_in_config` direttamente da
      `app.core.arricchisci.api_connector`.
    - `tests2/arricchisci/unit/test_api_connector_contract.py` importa
      `_nd` direttamente da `app.core.arricchisci.api_connector`.
  - Re-export in `api_connector.py` preserva entrambi i path direct
    import (verificato per identita' simbolica: `api_connector._nd is
    _api_connection_helpers._nd` post-extraction).

Behavior-preserving
===================
NESSUN cambio di crypto/decrypt policy:
  - `_encrypt_auth_in_config`: stesso `_crypto_encrypt(val)` sui field
    sensibili.
  - `_decrypt_auth_in_config`: stesso `strict` flow + `was_encrypted`
    check tramite `_crypto_is_encrypted` + `CredentialUnavailableError`
    raise.
  - `_nd`: stesso fallback `node.get('data', {}).get(key, default)`.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`api_connector.py` al momento di B3-B.1. Nessuna modifica di logica,
comment, ordine di operazione, sensitive field tuple, idempotency
semantics.
"""

from __future__ import annotations

from typing import Dict

from app.shared.crypto import (
    CredentialUnavailableError,
    decrypt as _crypto_decrypt,
    encrypt as _crypto_encrypt,
    is_encrypted as _crypto_is_encrypted,
)


# Campi sensibili nel blocco auth di una API connection — vengono cifrati
# at-rest. Restano in chiaro in memoria durante l'uso e vengono re-cifrati
# prima del save nel DB.
_AUTH_SENSITIVE_FIELDS = (
    "password",
    "client_secret",
    "token",
    "access_token",
    "refresh_token",
    "key_value",
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
