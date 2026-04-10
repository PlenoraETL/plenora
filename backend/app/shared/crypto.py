"""
app/shared/crypto.py
====================
Modulo centralizzato per la cifratura simmetrica di campi sensibili a riposo
(password DB, token API, secret OAuth, client_secret datasource, ecc.).

Architettura
------------
- Algoritmo: Fernet (AES-128-CBC + HMAC-SHA256, IV random per messaggio).
- Chiave: derivata via SHA-256 da una env var (vedi `_get_raw_key()`).
- Formato del valore cifrato: ``"enc:<base64token>"``.
  Il prefisso ``enc:`` permette di:
    1. distinguere a colpo d'occhio i valori cifrati da quelli in chiaro,
    2. supportare backward-compat (i valori privi di prefisso vengono
       considerati legacy plaintext e restituiti tali e quali in lettura),
    3. cifrare in modo idempotente (encrypt() su un valore già cifrato è
       no-op, così le migrazioni e i save ripetuti non doppio-cifrano).

Sorgente della chiave (in ordine di priorità)
---------------------------------------------
1. ``URBY_ENCRYPTION_KEY``    — nome canonico nuovo (consigliato per nuovi deploy)
2. ``CONN_ENCRYPT_KEY``       — nome legacy già usato da connection_service.py
3. ``SECRET_KEY`` / ``JWT_SECRET_KEY`` — fallback condiviso con JWT auth

Comportamento se la chiave manca
--------------------------------
- In produzione (``ENVIRONMENT=production`` o ``ENV=prod``): **fail-fast**
  con ``RuntimeError`` esplicito. NON c'è fallback hardcoded.
- In sviluppo: viene usata una chiave di default *con warning rumoroso*.
  Questo permette di lavorare in locale senza configurare nulla, ma se
  cambi la chiave dopo aver cifrato dei valori, NON potrai più decifrarli.

Uso tipico
----------
    from app.shared.crypto import encrypt, decrypt, is_encrypted

    # in save:
    config["auth"]["password"] = encrypt(config["auth"]["password"])

    # in load:
    config["auth"]["password"] = decrypt(config["auth"]["password"])

    # check idempotente:
    if not is_encrypted(value):
        value = encrypt(value)

Compatibilità con il vecchio codice in connection_service.py
------------------------------------------------------------
La funzione di derivazione chiave è identica a quella usata in passato in
``app/services/connection_service.py`` (sha256 della raw key → urlsafe_b64
→ Fernet). I valori cifrati con quel codice vengono decifrati senza problemi
da questo modulo. Vedi anche test in ``tests_backend/unit/security/``.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ─── Costanti ────────────────────────────────────────────────────────────────

#: Prefisso che marca un valore come cifrato. Sceglie ``enc:`` (no versione)
#: per coerenza con il formato già presente in connection_service.py legacy.
ENC_PREFIX = "enc:"

#: Chiave di default usata SOLO in sviluppo se nessuna env var è impostata.
#: NON usare in produzione: l'app fa fail-fast quando ENVIRONMENT=production.
_DEV_DEFAULT_KEY = "urby-etl-default-key-change-in-production"

#: Cache del Fernet istanziato. Evita di ricalcolare la derivazione SHA-256
#: a ogni chiamata. Resettabile per i test via ``_reset_cache_for_tests()``.
_FERNET_CACHE: Optional[Fernet] = None


# ─── Helper interni ──────────────────────────────────────────────────────────

#: Lunghezza minima accettabile per una chiave (in caratteri).
#: Chiavi più corte sono troppo deboli e probabilmente errori di configurazione.
_MIN_KEY_LENGTH = 16


def _get_raw_key() -> str:
    """Recupera la raw key dall'env var, con fallback dev e fail-fast in prod.

    Cerca in ordine: URBY_ENCRYPTION_KEY → CONN_ENCRYPT_KEY → SECRET_KEY →
    JWT_SECRET_KEY. Se nessuna è valorizzata e ci troviamo in produzione,
    solleva ``RuntimeError`` (preferito a SystemExit per essere catturabile
    nei test e gestibile dal framework FastAPI in fase di startup).

    SECURITY hardening (Block F-D):
      - Se la chiave configurata è uguale a `_DEV_DEFAULT_KEY` (qualcuno
        ha copiato il valore di test in prod), trattiamo come "nessuna
        chiave configurata" → fail-fast in production
      - Se la chiave è più corta di `_MIN_KEY_LENGTH` caratteri, fail-fast
        anche in dev (probabile errore di configurazione)
      - In dev, se URBY_DISABLE_DEFAULT_KEY=1 è impostata, fail-fast invece
        di usare la dev key (opt-in per chi vuole replicare prod localmente)
    """
    found = None
    for env_name in (
        "URBY_ENCRYPTION_KEY",
        "CONN_ENCRYPT_KEY",
        "SECRET_KEY",
        "JWT_SECRET_KEY",
    ):
        val = os.getenv(env_name)
        if val:
            found = (env_name, val)
            break

    env = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()
    is_prod = env in ("production", "prod")

    if found:
        env_name, val = found
        # Detection del default-key che qualcuno ha copiato in prod
        if val == _DEV_DEFAULT_KEY:
            msg = (
                f"[URBY-ETL] {env_name} contiene il valore di default di sviluppo. "
                f"Questo è un grave rischio di sicurezza: cambia la chiave."
            )
            if is_prod:
                raise RuntimeError(msg)
            logger.warning(msg)
        # Chiavi troppo corte sono inaccettabili in qualsiasi ambiente
        if len(val) < _MIN_KEY_LENGTH:
            raise RuntimeError(
                f"[URBY-ETL] {env_name} troppo corta ({len(val)} char, minimo "
                f"{_MIN_KEY_LENGTH}). Usa una chiave casuale di almeno "
                f"{_MIN_KEY_LENGTH} caratteri."
            )
        return val

    # Nessuna chiave trovata
    if is_prod:
        raise RuntimeError(
            "[URBY-ETL] Nessuna chiave di cifratura configurata. "
            "Imposta URBY_ENCRYPTION_KEY (o CONN_ENCRYPT_KEY/SECRET_KEY) "
            "nelle variabili d'ambiente prima di avviare in produzione."
        )

    # Opt-in per developer che vogliono replicare il fail-fast di prod
    if os.getenv("URBY_DISABLE_DEFAULT_KEY") == "1":
        raise RuntimeError(
            "[URBY-ETL] Nessuna chiave di cifratura e URBY_DISABLE_DEFAULT_KEY=1. "
            "Imposta URBY_ENCRYPTION_KEY o rimuovi URBY_DISABLE_DEFAULT_KEY."
        )

    logger.warning(
        "[URBY-ETL] Nessuna chiave di cifratura trovata nelle env var: "
        "uso la chiave di sviluppo di default. NON usare in produzione."
    )
    return _DEV_DEFAULT_KEY


def _get_fernet() -> Fernet:
    """Costruisce (o recupera dalla cache) l'istanza Fernet."""
    global _FERNET_CACHE
    if _FERNET_CACHE is not None:
        return _FERNET_CACHE
    raw_key = _get_raw_key()
    # SHA-256 → 32 byte → urlsafe_b64 = formato accettato da Fernet
    key_bytes = hashlib.sha256(raw_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    _FERNET_CACHE = Fernet(fernet_key)
    return _FERNET_CACHE


def _reset_cache_for_tests() -> None:
    """Reset della cache Fernet, da usare nei test che cambiano env var."""
    global _FERNET_CACHE
    _FERNET_CACHE = None


# ─── API pubblica ────────────────────────────────────────────────────────────

def is_encrypted(value: Optional[str]) -> bool:
    """True se ``value`` è una stringa con il prefisso di cifratura."""
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt(value: Optional[str]) -> Optional[str]:
    """Cifra un valore stringa con Fernet.

    - ``None`` o stringa vuota → restituiti tal quali (nulla da cifrare).
    - Valore già cifrato (con prefisso ``enc:``) → restituito tal quale
      (idempotente, sicuro da chiamare in cascata).
    - Errori di cifratura → log warning + restituzione del valore originale
      (preferiamo non bloccare il save di una connessione per un bug crypto;
      l'utente vede comunque il warning nei log).
    """
    if value is None or value == "":
        return value
    if is_encrypted(value):
        return value
    try:
        token = _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
        return f"{ENC_PREFIX}{token}"
    except Exception as e:  # pragma: no cover — fallback difensivo
        logger.warning(f"crypto.encrypt fallita: {e}")
        return value


def decrypt(value: Optional[str]) -> Optional[str]:
    """Decifra un valore precedentemente cifrato con :func:`encrypt`.

    - ``None`` o stringa vuota → restituiti tal quali.
    - Valore in chiaro (no prefisso ``enc:``) → restituito tal quale.
      *Backward-compat con i record legacy non ancora migrati.*
    - Token cifrato non decifrabile (chiave sbagliata, dato corrotto) →
      log warning + restituzione del valore originale (l'app continua a
      girare ma la connessione di terze parti fallirà al primo uso, dando
      all'utente un errore visibile invece di un crash silenzioso).
    """
    if value is None or value == "":
        return value
    if not is_encrypted(value):
        return value  # legacy plaintext
    token = value[len(ENC_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.warning(
            "crypto.decrypt: token Fernet non valido (chiave sbagliata o "
            "valore corrotto). Restituisco il valore cifrato non decodificato."
        )
        return value
    except Exception as e:  # pragma: no cover
        logger.warning(f"crypto.decrypt fallita: {e}")
        return value


def encrypt_dict_fields(d: dict, fields: list[str]) -> dict:
    """Cifra in-place i campi specificati di un dict (utility per save).

    Salta silenziosamente i campi assenti o non-string. Modifica e
    restituisce il dict per comodità in chain.
    """
    for f in fields:
        if f in d and isinstance(d.get(f), str):
            d[f] = encrypt(d[f])
    return d


def decrypt_dict_fields(d: dict, fields: list[str]) -> dict:
    """Decifra in-place i campi specificati di un dict (utility per load)."""
    for f in fields:
        if f in d and isinstance(d.get(f), str):
            d[f] = decrypt(d[f])
    return d
