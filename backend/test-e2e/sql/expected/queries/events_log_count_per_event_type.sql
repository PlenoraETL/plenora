-- Distribuzione di event_type su 10000 righe in events_log.
-- Generata in 02_volume.sql con CASE (id % 4) → 4 valori equiprobabili.
-- 10000 / 4 = 2500 per ciascun event_type.
-- Riferimento: test-e2e/sql/expected/README.md sezione 9.2.

SELECT event_type,
       COUNT(*) AS n
FROM events_log
GROUP BY event_type
ORDER BY event_type;
