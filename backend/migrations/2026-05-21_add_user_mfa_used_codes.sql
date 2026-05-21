-- TOTP replay prevention per-utente.
--
-- Motivazione:
--   RFC 6238 §5 richiede che ogni codice TOTP sia usato una sola volta.
--   `pyotp.TOTP.verify(valid_window=1)` non e' stateful: lo stesso codice
--   puo' essere riutilizzato entro la finestra di 90s (±1 step) se un
--   attaccante lo cattura via shoulder-surfing, phishing della seconda
--   pagina del login, o MitM su una connessione non-TLS.
--
-- Questa tabella serve come blacklist short-lived: dopo ogni verify
-- riuscito, il code-hash + user_id viene registrato. La PK
-- (user_id, code_hash) garantisce che un secondo INSERT con lo stesso
-- valore fallisca (ON CONFLICT DO NOTHING → 0 righe affette → replay).
--
-- TTL: gestito via `expires_at` (NOW + 90s tipico). Le righe scadute
-- vengono ignorate dalle query e ripulite da `vacuum_expired_codes`
-- (chiamata dal job di healthcheck o lazy alla prossima verify).
--
-- Niente backup codes qui: gestiti separatamente (futura migration).
-- Niente Redis: il volume e' basso (1 row per ogni login MFA, TTL 90s),
-- la PG transactional consistency e' preferibile a una dipendenza extra.

CREATE TABLE IF NOT EXISTS plenora.user_mfa_used_codes (
    user_id     TEXT        NOT NULL REFERENCES plenora.users(id) ON DELETE CASCADE,
    code_hash   TEXT        NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, code_hash)
);

-- Indice sul TTL per il VACUUM periodico: cancellare le righe scadute
-- senza scan full table.
CREATE INDEX IF NOT EXISTS user_mfa_used_codes_expires_at_idx
    ON plenora.user_mfa_used_codes (expires_at);
