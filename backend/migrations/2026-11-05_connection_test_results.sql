-- Esito dell'ultimo «Test connessione» di ogni connessione salvata.
--
-- Regola applicativa (app/shared/connection_test_results.py): ogni endpoint
-- che testa una connessione salvata (database, ArcGIS, Data Source, API)
-- registra qui l'esito con UNA sola funzione: riuscito o no, messaggio
-- senza segreti, data/ora UTC, utente, durata. Una modifica salvata o la
-- cancellazione della connessione azzera l'esito (reset_at): un test
-- partito prima dell'azzeramento non lo sovrascrive.
--
-- Una riga per (tipo, id). Nessuna chiave esterna verso le quattro tabelle
-- delle connessioni (la riga e' polimorfa) ne' verso users (l'utente si
-- conserva anche per nome: tested_by_name resta leggibile se l'utente viene
-- cancellato).
--
-- SICUREZZA DELL'INSTALLAZIONE: solo CREATE ... IF NOT EXISTS su una tabella
-- nuova e vuota; nessun dato esistente viene letto o modificato, quindi la
-- migrazione non puo' fallire per lo stato del DB e si puo' rieseguire.
CREATE TABLE IF NOT EXISTS connection_test_results (
    resource_kind   TEXT NOT NULL,
    resource_id     TEXT NOT NULL,
    success         BOOLEAN,
    message         TEXT,
    tested_at       TIMESTAMPTZ,
    tested_by       TEXT,
    tested_by_name  TEXT,
    duration_ms     INTEGER,
    reset_at        TIMESTAMPTZ,
    PRIMARY KEY (resource_kind, resource_id)
);
