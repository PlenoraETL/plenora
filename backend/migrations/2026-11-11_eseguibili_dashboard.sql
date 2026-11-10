-- Dashboard Grafana degli Eseguibili (11/11/2026).
--
-- Una dashboard Grafana per Eseguibile, legata 1:1: dashboard_uid e' l'uid
-- della dashboard in Grafana (app/core/grafana); NULL = nessuna dashboard.
-- La crea chi puo' modificare l'Eseguibile (alla pubblicazione con
-- create_dashboard o dopo); ripubblicare non la tocca; eliminare l'Eseguibile
-- la elimina in Grafana (best effort).
--
-- SICUREZZA DELL'INSTALLAZIONE: solo ADD COLUMN IF NOT EXISTS, nullable,
-- senza default da riscrivere: nessun dato esistente viene letto o
-- modificato, si puo' rieseguire.

ALTER TABLE eseguibili
    ADD COLUMN IF NOT EXISTS dashboard_uid TEXT;
