"""
MFA TOTP (RFC 6238) — enrollment, verify, disable.

Policy
------
- Opt-in stretto: nessuna enforcement globale, solo gli utenti che chiamano
  /enroll attivano MFA. Il login standard e' invariato se MFA non e' attiva.
- Two-step enrollment:
    1. /enroll crea un secret con enabled=False (pendente).
    2. /verify con codice TOTP corretto setta enabled=True.
  Se l'utente abbandona senza /verify, un successivo /enroll sovrascrive
  il secret pendente. Questo evita righe "zombie" di enrollment non
  confermati che si accumulano.
- Disable richiede codice TOTP valido (proof-of-possession).

Storage
-------
Tabella `plenora.user_mfa` (vedi migration 2026-04-21_add_user_mfa.sql).
Secret cifrato con Fernet via `app.shared.crypto`. La chain della chiave e'
`PLENORA_ENCRYPTION_KEY` → `URBY_ENCRYPTION_KEY` → `CONN_ENCRYPT_KEY`;
encrypt() e' idempotente.

Trade-off noto: mfa_required leak nel login
-------------------------------------------
Il login ritorna 403 `mfa_required` quando password e' corretta ma MFA
assente dal body. Differisce da 401 per password errata. Un attaccante con
password valida impara "MFA attiva per questo account". E' un leak
accettato in quasi tutti i sistemi MFA via API: l'alternativa (401 generico
sempre) romperebbe l'UX del client MFA-aware. Rate limit login gia' in
vigore (LOGIN_MAX_ATTEMPTS + LOGIN_LOCKOUT_SECONDS). Documentato in ADR
dedicato (TODO: ADR-0002).
"""
from __future__ import annotations

import base64
import io
from typing import Optional, TypedDict

import pyotp
import qrcode

from app.db import pg_connection
from app.shared.crypto import decrypt, encrypt
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Issuer label mostrato nell'authenticator app (Google Authenticator ecc.).
TOTP_ISSUER = "URBY-ETL"

# Tolleranza drift orologio: accetta codice precedente/successivo (+/- 30s).
# RFC 6238 §5.2 permette max 1 step di drift. Valori >1 aumentano la finestra
# di replay e non sono raccomandati.
TOTP_WINDOW = 1


class MFAEnrollment(TypedDict):
    secret: str
    provisioning_uri: str
    qr_code_png_base64: str


def _row_for(user_id: str) -> Optional[dict]:
    return pg_connection.fetchone(
        "SELECT user_id, secret_encrypted, enabled, enrolled_at, last_verified_at "
        "FROM plenora.user_mfa WHERE user_id = ?",
        (user_id,),
    )


def is_enabled(user_id: str) -> bool:
    """True se l'utente ha MFA attiva (riga presente con enabled=TRUE).

    Usata dal /login per decidere se richiedere mfa_code. Deve essere
    veloce: una singola fetchone sulla PK.
    """
    row = _row_for(user_id)
    return bool(row and row.get("enabled"))


def generate_enrollment(user_id: str, username: str) -> MFAEnrollment:
    """Genera un nuovo secret TOTP per user_id (enrollment pendente).

    Crea o sovrascrive la riga in `user_mfa` con enabled=FALSE.
    La riga va confermata con `verify_enrollment(user_id, code)` prima
    di essere considerata attiva.

    Sovrascrittura: se esiste una riga (pendente o attiva), viene
    sostituito il secret e resetta enabled=FALSE + enrolled_at=NULL.
    Questo e' il caso d'uso "voglio ri-enrollare perche' ho perso il
    device": l'utente ri-enrolla, verifica col nuovo secret, ora e'
    di nuovo attivo. Attenzione: finche' non chiama /verify, l'utente
    perde l'accesso MFA che aveva, quindi e' una operazione
    "distruttiva" sul proprio enrollment. Documentata in endpoint.
    """
    secret = pyotp.random_base32()
    secret_encrypted = encrypt(secret)

    pg_connection.execute(
        """
        INSERT INTO plenora.user_mfa (user_id, secret_encrypted, enabled, created_at, updated_at)
        VALUES (?, ?, FALSE, NOW(), NOW())
        ON CONFLICT (user_id) DO UPDATE
        SET secret_encrypted = EXCLUDED.secret_encrypted,
            enabled = FALSE,
            enrolled_at = NULL,
            updated_at = NOW()
        """,
        (user_id, secret_encrypted),
    )

    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=username, issuer_name=TOTP_ISSUER)

    # QR generato server-side come convenienza. Il client puo' ignorare
    # il PNG e generare il proprio QR dal provisioning_uri (piu' leggero).
    qr = qrcode.QRCode()
    qr.add_data(provisioning_uri)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image().save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    logger.info(f"MFA enrollment generated for user {user_id}", op="mfa_enroll")
    return {
        "secret": secret,
        "provisioning_uri": provisioning_uri,
        "qr_code_png_base64": qr_b64,
    }


def verify_code(user_id: str, code: str) -> bool:
    """Verifica un codice TOTP contro il secret dell'utente.

    - Row assente → False.
    - Codice errato → False.
    - Codice corretto → True + aggiorna `last_verified_at`.
    - NON modifica `enabled`: l'attivazione dell'enrollment e' gestita
      da `verify_enrollment()`. Questa funzione e' il verificatore
      comune usato anche dal /login (dove la riga deve gia' essere
      enabled=TRUE, verificato dal caller).
    """
    if not code or not code.isdigit():
        return False
    row = _row_for(user_id)
    if not row:
        return False
    secret = decrypt(row["secret_encrypted"])
    if not secret:
        # decrypt fallito (chiave cambiata, valore corrotto): non
        # confermiamo. No crash: fail-closed sull'MFA e' preferibile
        # a un errore 500 che leak informazione.
        logger.warning(f"MFA decrypt failed for user {user_id}", op="mfa_decrypt_fail")
        return False
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=TOTP_WINDOW):
        return False
    pg_connection.execute(
        "UPDATE plenora.user_mfa SET last_verified_at = NOW(), updated_at = NOW() "
        "WHERE user_id = ?",
        (user_id,),
    )
    return True


def verify_enrollment(user_id: str, code: str) -> bool:
    """Conferma l'enrollment: verifica codice + setta enabled=TRUE.

    Idempotente: se la riga e' gia' enabled=TRUE e il codice e' valido,
    ritorna True senza cambiare `enrolled_at` (preservato via COALESCE).
    """
    if not verify_code(user_id, code):
        return False
    pg_connection.execute(
        "UPDATE plenora.user_mfa "
        "SET enabled = TRUE, enrolled_at = COALESCE(enrolled_at, NOW()), updated_at = NOW() "
        "WHERE user_id = ?",
        (user_id,),
    )
    logger.info(f"MFA enrollment activated for user {user_id}", op="mfa_enroll_activated")
    return True


def disable(user_id: str, code: str) -> bool:
    """Disabilita MFA per l'utente. Richiede codice TOTP valido.

    Proof-of-possession: non basta essere autenticati (password o JWT),
    bisogna anche avere l'authenticator. Questo evita che un token
    rubato possa rimuovere MFA.

    La riga viene cancellata: la prossima /enroll parte da zero.
    """
    if not verify_code(user_id, code):
        return False
    pg_connection.execute(
        "DELETE FROM plenora.user_mfa WHERE user_id = ?",
        (user_id,),
    )
    logger.info(f"MFA disabled for user {user_id}", op="mfa_disable")
    return True


def status(user_id: str) -> dict:
    """Snapshot dello stato MFA per l'utente.

    Ritorna sempre un dict (mai None). Campi `enrolled_at` e
    `last_verified_at` ISO-8601 se presenti, altrimenti None.
    """
    row = _row_for(user_id)
    if not row:
        return {"enabled": False, "enrolled_at": None, "last_verified_at": None}
    enrolled_at = row.get("enrolled_at")
    last_verified_at = row.get("last_verified_at")
    return {
        "enabled": bool(row.get("enabled")),
        "enrolled_at": enrolled_at.isoformat() if enrolled_at else None,
        "last_verified_at": last_verified_at.isoformat() if last_verified_at else None,
    }
