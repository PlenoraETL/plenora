-- Conteggio righe events_log con value NULL.
-- 02_volume.sql imposta value=NULL per ogni i divisibile per 5
-- → 10000 / 5 = 2000 righe NULL.
-- Riferimento: test-e2e/sql/expected/README.md sezioni 9.1 e 9.4.

SELECT COUNT(*) AS n_total,
       COUNT(value) AS n_non_null,
       COUNT(*) - COUNT(value) AS n_null
FROM events_log;
