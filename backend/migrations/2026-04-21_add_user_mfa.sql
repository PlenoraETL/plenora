-- MFA TOTP (RFC 6238) per-utente — opt-in stretto.
--
-- Motivazione:
--   ISO 27001 A.9.4.2 (secure log-on procedures) richiede autenticazione
--   forte per ruoli privilegiati. Implementazione: TOTP via authenticator
--   app (Google Authenticator, Authy, ecc.), RFC 6238, SHA-1 HMAC, step 30s.
--
-- Scelte di schema:
--   - Tabella separata (NO ALTER users): user_mfa ha lifecycle indipendente
--     (enrollment prima di attivazione, disable tiene la riga o la rimuove).
--     Separare il secret dal record utente riduce anche il blast radius in
--     caso di SELECT * accidentale su users.
--   - PK = user_id (FK CASCADE a users): una sola enrollment attiva per
--     utente. Un nuovo enroll sovrascrive il precedente (vedi mfa.py).
--     CASCADE: cancellare un utente deve cancellare il suo secret.
--   - secret_encrypted TEXT NOT NULL: base32 TOTP secret, cifrato con
--     Fernet via app.infrastructure.security.crypto (chain `PLENORA_ENCRYPTION_KEY` →
--     `URBY_ENCRYPTION_KEY` → `CONN_ENCRYPT_KEY`). Il prefisso `enc:` e'
--     parte del valore, il decrypt e' idempotente su plaintext legacy
--     (ma qui non dovrebbero mai esistere plaintext: tutte le INSERT
--     passano per encrypt()).
--   - enabled BOOLEAN DEFAULT FALSE: enrollment e' two-step. /enroll
--     crea la riga con enabled=FALSE; /verify con codice corretto
--     setta enabled=TRUE. Se /verify fallisce la riga resta pendente
--     e puo' essere sovrascritta da un nuovo /enroll.
--   - enrolled_at TIMESTAMPTZ: valorizzata quando enabled diventa TRUE.
--     NULL finche' l'enrollment non e' attivo. Serve per audit
--     ("da quando l'utente X ha MFA attiva").
--   - last_verified_at TIMESTAMPTZ: ultimo TOTP valido presentato
--     (login con MFA o disable). Serve per detection di account non
--     usati da N giorni.
--   - created_at / updated_at: standard.
--
-- Niente backup codes in questo schema: scelta di scope. Il recupero in
-- caso di device perso passa per admin reset (disable MFA via admin API,
-- utente ri-enrolla). Se in futuro serviranno backup codes, una nuova
-- migration aggiungera' plenora.user_mfa_backup_codes (tabella 1-N,
-- code_hash bcrypt, single-use).
--
-- Niente INDICE oltre la PK: tutte le query sono per user_id, la PK
-- basta.
--
-- Idempotente: IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS plenora.user_mfa (
    user_id           TEXT PRIMARY KEY REFERENCES plenora.users(id) ON DELETE CASCADE,
    secret_encrypted  TEXT NOT NULL,
    enabled           BOOLEAN NOT NULL DEFAULT FALSE,
    enrolled_at       TIMESTAMPTZ,
    last_verified_at  TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
