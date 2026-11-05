-- Unicita' dello username in forma normalizzata, garantita dal DB.
--
-- Regola applicativa (shared.contracts.auth.normalized_username): due utenti
-- non possono avere lo stesso username dopo trim + casefold. create_user la
-- controlla e poi fa l'INSERT: senza un vincolo nel DB due creazioni
-- concorrenti possono passare entrambe il controllo. Questo indice chiude la
-- finestra; create_user traduce la sua violazione nello stesso 409
-- username_already_exists del controllo applicativo.
--
-- Forma nel DB: lower(btrim(username)). NON coincide con casefold di Python:
--   - casefold piega «ß» in «ss» (e altre piegature estese), lower no;
--   - lower dipende dal LC_CTYPE del database (con locale C piega solo ASCII);
--   - btrim toglie solo gli spazi, strip di Python ogni spazio Unicode (la
--     forma valida dello username vieta comunque spazi iniziali/finali).
-- L'indice e' quindi una rete piu' stretta della regola: copre i casi comuni
-- (maiuscole/minuscole ASCII e latine) anche sotto concorrenza; il controllo
-- applicativo resta e copre casefold.
--
-- SICUREZZA DELL'INSTALLAZIONE: se nel DB esistono gia' username che
-- collidono sulla forma normalizzata, l'indice NON viene creato e la
-- migrazione NON fallisce (l'installazione prosegue). Viene emesso un
-- WARNING con i doppioni, riportato nel log del backend dal runner delle
-- migrazioni («[MIGRATIONS] ... WARNING ...»), e lo stato sistema admin
-- (GET /api/admin/system-status, blocco db_schema) segnala l'indice assente.
-- Rimedio: rinominare/eliminare i doppioni, poi
--   DELETE FROM plenora.schema_migrations
--    WHERE filename = '2026-11-05_users_username_normalized_unique.sql';
-- e riavviare il backend (la migrazione e' idempotente).
DO $$
DECLARE
    collisions TEXT;
BEGIN
    IF to_regclass('plenora.ux_users_username_normalized') IS NOT NULL THEN
        RETURN;
    END IF;

    SELECT string_agg(
               format('%s -> [%s]', normalized, usernames),
               '; ' ORDER BY normalized
           )
      INTO collisions
      FROM (
          SELECT lower(btrim(username)) AS normalized,
                 string_agg(username, ', ' ORDER BY username) AS usernames
            FROM plenora.users
           GROUP BY lower(btrim(username))
          HAVING count(*) > 1
      ) AS dup;

    IF collisions IS NULL THEN
        CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_normalized
            ON plenora.users (lower(btrim(username)));
    ELSE
        RAISE WARNING 'ux_users_username_normalized NON creato: username doppioni sulla forma normalizzata: %. Risolvi i doppioni, cancella la riga di questa migrazione da plenora.schema_migrations e riavvia il backend.', collisions;
    END IF;
END $$;
