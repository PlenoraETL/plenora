"""TLS-bypass policy primitives.

Single source of truth for "quando e' accettabile usare verify=False sulle
chiamate HTTP outbound", condiviso tra i moduli che parlano con servizi
esterni (OAuth, ArcGIS, enricher REST, SFTP datasource).

# Policy

- **Default safe**: TLS verify viene forzato a True. Ogni tentativo da
  configurazione utente di settare `verify_ssl=False` viene loggato come
  WARNING di security e ignorato.
- **Opt-in esplicito**: il bypass e' permesso solo se UNO dei due signal
  e' presente:
    1. `PLENORA_ALLOW_TLS_BYPASS=1` env var (override intenzionale, ideale
       per CI/test fixtures con certificati self-signed)
    2. `ENVIRONMENT in (development, dev, test)` (backward-compat con il
       pattern di dev locale che gia' richiedeva verify_ssl=False per
       certificati self-signed Windows/macOS)
- **Prod-like fail-closed**: in `production|prod|staging|stage` il bypass
  e' SEMPRE rifiutato, anche con `PLENORA_ALLOW_TLS_BYPASS=1`. Le
  credenziali in canali di produzione non devono viaggiare su MITM-able.

# Threat model coperto

Operatore malevolo (admin compromesso, DB compromesso, leak credentials di
admin) che imposta `verify_ssl=False` nella connection config con
l'intenzione di intercettare credenziali tramite MITM in produzione.
Senza questo gate il prod-side `requests.post(..., verify=False)`
accettava qualsiasi certificato — inclusi quelli di un proxy MITM.

# Threat model NON coperto

- Compromissione del processo Python (RCE): l'attaccante puo' bypassare
  qualsiasi gate Python.
- Compromissione di un host_key di sistema: SFTP `host_key_policy` e' un
  concern parallelo gestito in `app/core/datasources/sftp.py` con la
  stessa euristica env-aware.
"""

from __future__ import annotations

import os
from typing import Any

from app.utils.logger import get_logger

logger = get_logger(__name__)

_PROD_ENVS = ("production", "prod", "staging", "stage")
_DEV_ENVS = ("development", "dev", "test")


def _env_name() -> str:
    return os.environ.get(
        "ENVIRONMENT", os.environ.get("ENV", "")
    ).lower()


def is_prod_like_env() -> bool:
    """True se ENVIRONMENT/ENV identifica produzione o staging."""
    return _env_name() in _PROD_ENVS


def tls_bypass_allowed() -> bool:
    """Indica se in questo deployment e' lecito chiamare con verify=False.

    Fail-safe by default: ritorna False quando la policy non puo' essere
    determinata con certezza (es. ENVIRONMENT vuoto e nessun opt-in).
    """
    if is_prod_like_env():
        return False
    if os.environ.get("PLENORA_ALLOW_TLS_BYPASS", "").strip() == "1":
        return True
    if _env_name() in _DEV_ENVS:
        return True
    return False


def resolve_tls_verify(verify_ssl_cfg: Any, *, context: str) -> bool:
    """Applica la policy a un flag user-supplied `verify_ssl`.

    Args:
        verify_ssl_cfg: valore raw dalla configurazione (auth dict,
            EnrichmentOptions, etc.). `None`/missing tratta come `True`.
        context: stringa human-readable per il log, identifica il caller
            (es. "ArcGIS token", "API enricher per-row").

    Returns:
        True se TLS verify deve essere applicato. False solo se l'utente
        l'ha richiesto E il bypass e' consentito in questo ambiente.

    Side effect:
        Logga un WARNING di security in due casi:
          - bypass richiesto ma bloccato (signal a security ops che
            qualcuno sta tentando MITM in prod);
          - bypass richiesto e concesso (signal in dev per non perdere
            il segnale completamente).
    """
    requested = bool(verify_ssl_cfg) if verify_ssl_cfg is not None else True
    if requested:
        return True
    if not tls_bypass_allowed():
        logger.warning(
            "[SECURITY] %s: verify_ssl=False ignorato (TLS bypass non "
            "consentito in questo ambiente) — forzato verify=True per "
            "evitare canale MITM-able su credenziali.",
            context,
        )
        return True
    logger.warning(
        "[SECURITY] %s: verify_ssl=False attivo (bypass abilitato via env). "
        "Le credenziali viaggiano senza verifica TLS — accettabile solo in "
        "dev/CI.",
        context,
    )
    return False
