-- Eliminare un utente mantenendo lo storico (10/11/2026).
--
-- Un utente referenziato dallo storico (esecuzioni, progetti, ticket, ...)
-- non si cancella fisicamente: la riga resta come «lapide», cosi' ogni
-- chiave esterna verso users resta valida per costruzione.
-- - deleted_at: quando e' stato eliminato; NULL = utente esistente. Le
--   letture operative (login, token, liste, conteggi) leggono solo le righe
--   con deleted_at IS NULL (app.shared.contracts.auth.NOT_DELETED_SQL);
-- - deleted_username: lo username originale. La colonna username diventa
--   «eliminato:<id>», cosi' il nome originale torna libero per un utente
--   nuovo; lo storico mostra «utente eliminato (<deleted_username>)».
--
-- SICUREZZA DELL'INSTALLAZIONE: solo ADD COLUMN IF NOT EXISTS, nullable,
-- senza default da riscrivere: nessun dato esistente viene letto o
-- modificato, si puo' rieseguire.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS deleted_username TEXT;
