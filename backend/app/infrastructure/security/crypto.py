"""
app/infrastructure/security/crypto.py

Modulo centralizzato per la cifratura simmetrica di campi sensibili a riposo
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.environment import environment_name, get_env
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CredentialUnavailableError(Exception):
    """Raised quando un secret cifrato a-rest non e' ricostruibile.

    Scenari coperti:
      - valore None nel DB ma obbligatorio per il consumer
      - prefisso ``enc:`` presente ma ``decrypt`` ritorna None (chiave
        mancante/sbagliata o token Fernet corrotto)

    Uso: i path di "test" (test_connection, test_api_connection,
    test_datasource) devono chiudersi con errore gestito invece di 500
    o 200-OK apparente. Il router traduce questa eccezione in HTTP 422.
    """


# Prefisso che marca un valore come cifrato. Sceglie ``enc:`` (no versione
# per coerenza con il formato già presente in connection_service.py legacy
ENC_PREFIX = "enc:"

# Stringa storica del default-key che era committata nel repo fino al
# 2026-05-21. Mantenuta SOLO come signature per il fail-fast: se qualcuno
# trova il valore in un .env legacy e lo copia in produzione, vogliamo
# crashare il boot. NON viene piu' usata come chiave attiva (vedi
# `_ephemeral_dev_key`). Threat coperto: chiunque clonando il repo
# poteva decifrare tutti i dati at-rest di installazioni dev/staging
# che lasciavano la default-key.
_LEGACY_COMMITTED_KEY_SIGNATURE = (
    "urby-etl-default-key-change-in-production"
)

# Cache del Fernet istanziato. Evita di ricalcolare la derivazione SHA-256
# a ogni chiamata. Resettabile per i test via ``_reset_cache_for_tests``
_FERNET_CACHE: Optional[Fernet] = None

# Chiave effimera generata al primo uso in dev/test quando manca
# `PLENORA_ENCRYPTION_KEY`. NON persistente tra restart: i dati cifrati
# con questa chiave non sopravvivono al riavvio dell'app. Per
# persistenza in dev locale, settare `PLENORA_ENCRYPTION_KEY` in `.env`.
_DEV_EPHEMERAL_KEY: Optional[str] = None


# Lunghezza minima accettabile per una chiave (in caratteri
# Chiavi più corte sono troppo deboli e probabilmente errori di configurazione
_MIN_KEY_LENGTH = 16


def _ephemeral_dev_key() -> str:
    """Genera (una sola volta per processo) una chiave random per dev/test.

    Pattern allineato a `JWT_SECRET_KEY` generata a runtime in
    `app/core/auth/auth.py`. La chiave NON viene persistita su disco e
    NON viene loggata: chi vuole stabilita' tra restart in locale deve
    settare `PLENORA_ENCRYPTION_KEY` in `backend/.env`.
    """
    global _DEV_EPHEMERAL_KEY
    if _DEV_EPHEMERAL_KEY is None:
        _DEV_EPHEMERAL_KEY = secrets.token_urlsafe(32)
        logger.warning(
            "[PLENORA-ETL] Nessuna PLENORA_ENCRYPTION_KEY configurata: "
            "generata chiave EFFIMERA per questa sessione. I campi cifrati "
            "a-rest non sopravviveranno al riavvio del processo. Per "
            "persistenza in dev locale, imposta PLENORA_ENCRYPTION_KEY "
            "in backend/.env (token_urlsafe 32 byte raccomandato)."
        )
    return _DEV_EPHEMERAL_KEY


def _get_raw_key() -> str:
    """Recupera la raw key dall'env var, con fallback dev e fail-fast in prod.

    Legge la env var canonical PLENORA_ENCRYPTION_KEY. Gli alias legacy
    URBY_ENCRYPTION_KEY e CONN_ENCRYPT_KEY non sono piu' supportati.
    SECRET_KEY / JWT_SECRET_KEY: quei nomi appartengono al dominio auth JWT,
    tenerli separati rompe l'accoppiamento rotazione-JWT ↔ decifratura-at-rest.

    SECURITY hardening:
      - In prod/staging, missing key o legacy committed-key signature →
        ``RuntimeError`` fail-fast.
      - In dev, missing key → chiave effimera generata in-process
        (`_ephemeral_dev_key`). Pre-fix usavamo una stringa committata
        nel sorgente, decifrabile da chiunque clonasse il repo.
      - Opt-in `PLENORA_DISABLE_DEFAULT_KEY=1` per dev che vogliono
        replicare il fail-fast di prod localmente.
      - `_MIN_KEY_LENGTH` enforce in qualsiasi ambiente.
    """
    env_name = "PLENORA_ENCRYPTION_KEY"
    val = get_env(env_name)

    env = environment_name()
    # Simmetrico a _DOTENV_DISABLED_ENVS di config.py:26: gli ambienti
    # hardened (prod + staging) fail-fast su missing key / legacy default.
    is_prod = env in ("production", "prod", "staging", "stage")

    if val:
        # Detection della stringa legacy che era committata nel repo:
        # se qualcuno l'ha copiata in un .env di prod, crash fast.
        if val == _LEGACY_COMMITTED_KEY_SIGNATURE:
            msg = (
                f"[PLENORA-ETL] {env_name} contiene il vecchio default-key "
                f"committato nel repo (rimosso 2026-05-21). E' considerato "
                f"compromesso: ruota la chiave usando `secrets.token_urlsafe(32)`."
            )
            if is_prod:
                raise RuntimeError(msg)
            logger.warning(msg)
        if len(val) < _MIN_KEY_LENGTH:
            raise RuntimeError(
                f"[PLENORA-ETL] {env_name} troppo corta ({len(val)} char, "
                f"minimo {_MIN_KEY_LENGTH}). Usa una chiave casuale di "
                f"almeno {_MIN_KEY_LENGTH} caratteri."
            )
        return val

    # Nessuna chiave trovata.
    if is_prod:
        raise RuntimeError(
            "[PLENORA-ETL] Nessuna chiave di cifratura configurata. "
            "Imposta PLENORA_ENCRYPTION_KEY nelle variabili d'ambiente "
            "prima di avviare in produzione."
        )

    # Opt-in per developer che vogliono replicare il fail-fast di prod.
    if get_env("PLENORA_DISABLE_DEFAULT_KEY") == "1":
        raise RuntimeError(
            "[PLENORA-ETL] Nessuna chiave di cifratura e "
            "PLENORA_DISABLE_DEFAULT_KEY=1. Imposta PLENORA_ENCRYPTION_KEY "
            "o rimuovi PLENORA_DISABLE_DEFAULT_KEY."
        )

    return _ephemeral_dev_key()


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
    """Reset della cache Fernet, da usare nei test che cambiano env var.

    Reset anche la chiave effimera dev per garantire isolamento tra test
    (un test che usa la chiave generata non deve inquinare i successivi).
    """
    global _FERNET_CACHE, _DEV_EPHEMERAL_KEY
    _FERNET_CACHE = None
    _DEV_EPHEMERAL_KEY = None


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
    - Valore in chiaro (no prefisso ``enc:``) → errore esplicito: i secret
      persistiti devono sempre essere cifrati.
    - Token cifrato non decifrabile (chiave sbagliata, dato corrotto) →
      log warning + restituzione del valore originale (l'app continua a
      girare ma la connessione di terze parti fallirà al primo uso, dando
      all'utente un errore visibile invece di un crash silenzioso).
    """
    if value is None or value == "":
        return value
    if not is_encrypted(value):
        raise RuntimeError(
            "crypto.decrypt refused plaintext secret; expected enc: payload"
        )
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
    ``decrypt`` ritorna None la funzione solleva ``CredentialUnavailableError``.
    Usato nei path di test per non propagare silenziosamente stato invalido.
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
