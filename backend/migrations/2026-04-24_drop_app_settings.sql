-- F5 (2026-04-24): drop tabella `app_settings` post rimozione wrappers DAL.
-- Post [p7-c1] zero reader/writer nel codice. Invariants §6 #8 F5
-- (rinvio sciolto dalla rivalutazione trasversale 2026-04-24:
-- sistema non in prod → zero rischio dati).
--
-- Simmetrico: rimozione CREATE TABLE da `app/db/config_schema.sql`.

DROP TABLE IF EXISTS app_settings;
