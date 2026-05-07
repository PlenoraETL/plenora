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

Sorgente della chiave
---------------------
``PLENORA_ENCRYPTION_KEY`` — nome canonico unico.

Gli alias legacy ``URBY_ENCRYPTION_KEY`` e ``CONN_ENCRYPT_KEY`` sono stati
rimossi il 2026-04-23 (D4 chiuso via O-D4-B rollout ``.env`` prod +
O-D4-C rimozione codice/doc).

La chain NON fallbacka su ``SECRET_KEY`` / ``JWT_SECRET_KEY``: quei nomi
appartengono al dominio auth JWT (vedi ``app/core/auth/auth.py``) e vengono
tenuti distinti di proposito. Condividere il secret tra JWT e crypto-at-rest
renderebbe la rotazione della chiave JWT (documentata in
``docs/ops/jwt-incident-response.md``) un'operazione silently distruttiva
per tutti i record cifrati. Un secret, un consumatore, un nome canonico.

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


class CredentialUnavailableError(Exception):
    """Raised quando un secret cifrato a-rest non e' ricostruibile.

    Scenari coperti:
      - valore None nel DB ma obbligatorio per il consumer
      - prefisso ``enc:`` presente ma ``decrypt()`` ritorna None (chiave
        mancante/sbagliata o token Fernet corrotto)

    Uso: i path di "test" (test_connection, test_api_connection,
    test_datasource) devono chiudersi con errore gestito invece di 500
    o 200-OK apparente. Il router traduce questa eccezione in HTTP 422.
    """


#: Prefisso che marca un valore come cifrato. Sceglie ``enc:`` (no versione)
#: per coerenza con il formato già presente in connection_service.py legacy.
ENC_PREFIX = "enc:"

#: Chiave di default usata SOLO in sviluppo se nessuna env var è impostata.
#: NON usare in produzione: l'app fa fail-fast quando ENVIRONMENT=production.
_DEV_DEFAULT_KEY = "urby-etl-default-key-change-in-production"

#: Cache del Fernet istanziato. Evita di ricalcolare la derivazione SHA-256
#: a ogni chiamata. Resettabile per i test via ``_reset_cache_for_tests()``.
_FERNET_CACHE: Optional[Fernet] = None


#: Lunghezza minima accettabile per una chiave (in caratteri).
#: Chiavi più corte sono troppo deboli e probabilmente errori di configurazione.
_MIN_KEY_LENGTH = 16


def _get_raw_key() -> str:
    """Recupera la raw key dall'env var, con fallback dev e fail-fast in prod.

    Legge la env var canonical PLENORA_ENCRYPTION_KEY. Gli alias legacy
    URBY_ENCRYPTION_KEY e CONN_ENCRYPT_KEY non sono piu' supportati
    (D4 chiuso 2026-04-23, vedi invariants.md §6). Nessun fallback su
    SECRET_KEY / JWT_SECRET_KEY: quei nomi appartengono al dominio auth JWT,
    tenerli separati rompe l'accoppiamento rotazione-JWT ↔ decifratura-at-rest.
    Se nessuna è valorizzata e ci troviamo in produzione,
    solleva ``RuntimeError`` (preferito a SystemExit per essere catturabile
    nei test e gestibile dal framework FastAPI in fase di startup).

    SECURITY hardening (Block F-D):
      - Se la chiave configurata è uguale a `_DEV_DEFAULT_KEY` (qualcuno
        ha copiato il valore di test in prod), trattiamo come "nessuna
        chiave configurata" → fail-fast in production
      - Se la chiave è più corta di `_MIN_KEY_LENGTH` caratteri, fail-fast
        anche in dev (probabile errore di configurazione)
      - In dev, se PLENORA_DISABLE_DEFAULT_KEY=1 è impostata, fail-fast invece
        di usare la dev key (opt-in per chi vuole replicare prod localmente)
    """
    # Unica env crypto supportata: PLENORA_ENCRYPTION_KEY. Gli alias legacy
    # URBY_ENCRYPTION_KEY e CONN_ENCRYPT_KEY sono stati rimossi il 2026-04-23
    # (D4 chiuso, vedi invariants.md §6).
    env_name = "PLENORA_ENCRYPTION_KEY"
    val = os.getenv(env_name)
    found = (env_name, val) if val else None

    env = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()
    # Simmetrico a _DOTENV_DISABLED_ENVS di config.py:26 — entrambi gli
    # ambienti "hardened" (prod + staging) devono fail-fast su missing
    # key / dev-default. Chiude S1 runtime_config_audit.md.
    is_prod = env in ("production", "prod", "staging", "stage")

    if found:
        env_name, val = found
        # Detection del default-key che qualcuno ha copiato in prod
        if val == _DEV_DEFAULT_KEY:
            msg = (
                f"[PLENORA-ETL] {env_name} contiene il valore di default di sviluppo. "
                f"Questo è un grave rischio di sicurezza: cambia la chiave."
            )
            if is_prod:
                raise RuntimeError(msg)
            logger.warning(msg)
        # Chiavi troppo corte sono inaccettabili in qualsiasi ambiente
        if len(val) < _MIN_KEY_LENGTH:
            raise RuntimeError(
                f"[PLENORA-ETL] {env_name} troppo corta ({len(val)} char, minimo "
                f"{_MIN_KEY_LENGTH}). Usa una chiave casuale di almeno "
                f"{_MIN_KEY_LENGTH} caratteri."
            )
        return val

    # Nessuna chiave trovata
    if is_prod:
        raise RuntimeError(
            "[PLENORA-ETL] Nessuna chiave di cifratura configurata. "
            "Imposta PLENORA_ENCRYPTION_KEY nelle variabili d'ambiente "
            "prima di avviare in produzione."
        )

    # Opt-in per developer che vogliono replicare il fail-fast di prod.
    if os.getenv("PLENORA_DISABLE_DEFAULT_KEY") == "1":
        raise RuntimeError(
            "[PLENORA-ETL] Nessuna chiave di cifratura e PLENORA_DISABLE_DEFAULT_KEY=1. "
            "Imposta PLENORA_ENCRYPTION_KEY o rimuovi PLENORA_DISABLE_DEFAULT_KEY."
        )

    logger.warning(
        "[PLENORA-ETL] Nessuna chiave di cifratura trovata nelle env var: "
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


def is_encrypted(value: Optional[str]) -> bool:
    """True se ``value`` è una stringa con il prefisso di cifratura."""
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt(value: Optional[str]) -> Optional[str]:
    """Cifra un valore stringa con Fernet.

    - ``None`` o stringa vuota → restituiti tal quali (nulla da cifrare).
    - Valore già cifrato (con prefisso ``enc:``) → restituito tal quale
      (idempotente, sicuro da chiamare in cascata).
    - Errori di cifratura → **fail-fast** con ``RuntimeError``: si rifiuta
      esplicitamente di salvare plaintext su dato critico (password, token,
      client_secret). Preferiamo il crash visibile al silent-compromise.
    """
    if value is None or value == "":
        return value
    if is_encrypted(value):
        return value
    try:
        token = _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
        return f"{ENC_PREFIX}{token}"
    except Exception as e:
        logger.error(f"crypto.encrypt fallita: {e}", exc_info=True)
        raise RuntimeError(
            "crypto.encrypt broken — refusing to save plaintext"
        ) from e


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
        logger.error(
            "crypto.decrypt: token Fernet non valido (chiave sbagliata o "
            "valore corrotto). Restituisco None."
        )
        return None
    except Exception as e:  # pragma: no cover
        logger.error(f"crypto.decrypt fallita: {e}", exc_info=True)
        return None


def encrypt_dict_fields(d: dict, fields: list[str]) -> dict:
    """Cifra in-place i campi specificati di un dict (utility per save).

    Salta silenziosamente i campi assenti o non-string. Modifica e
    restituisce il dict per comodità in chain.
    """
    for f in fields:
        if f in d and isinstance(d.get(f), str):
            d[f] = encrypt(d[f])
    return d


def decrypt_dict_fields(d: dict, fields: list[str], *, strict: bool = False) -> dict:
    """Decifra in-place i campi specificati di un dict (utility per load).

    Se ``strict=True``, per ogni campo con prefisso ``enc:`` il cui
    ``decrypt()`` ritorna None la funzione solleva ``CredentialUnavailableError``.
    Usato nei path di test per non propagare silenziosamente stato invalido
    (vedi Bug #1 + #2 del 2026-04-23).
    """
    for f in fields:
        if f in d and isinstance(d.get(f), str):
            val = d[f]
            was_encrypted = is_encrypted(val)
            decrypted = decrypt(val)
            if strict and was_encrypted and decrypted is None:
                raise CredentialUnavailableError(
                    f"campo '{f}' non decriptabile (chiave mancante/sbagliata o token corrotto)"
                )
            d[f] = decrypted
    return d
