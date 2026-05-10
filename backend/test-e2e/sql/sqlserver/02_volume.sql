-- Plenora E2E test dataset — volume SQL Server
-- Genera 10000 righe in events_log per stress test.
-- Usa CTE ricorsiva con OPTION (MAXRECURSION 0).
--
-- Distribuzione deterministica (no random):
--   customer_id  = ((id-1) % 12) + 1                 (cycle 1..12, FK valida)
--   event_type   = id % 4                            (view/click/purchase/logout)
--   occurred_at  = '2024-01-01' + (id % 365) days
--   value        = NULL ogni 5° riga, altrimenti id * 0.10

WITH seq AS (
    SELECT CAST(1 AS BIGINT) AS i
    UNION ALL
    SELECT i + 1 FROM seq WHERE i < 10000
)
INSERT INTO events_log (id, customer_id, event_type, occurred_at, value)
SELECT
    i AS id,
    CAST(((i - 1) % 12) + 1 AS INT) AS customer_id,
    CASE (i % 4)
        WHEN 0 THEN 'view'
        WHEN 1 THEN 'click'
        WHEN 2 THEN 'purchase'
        ELSE        'logout'
    END AS event_type,
    DATEADD(DAY, CAST(i % 365 AS INT), CAST('2024-01-01 00:00:00' AS DATETIME2)) AS occurred_at,
    CASE WHEN (i % 5) = 0 THEN NULL
         ELSE CAST(i * 0.10 AS DECIMAL(12, 2))
    END AS value
FROM seq
OPTION (MAXRECURSION 0);
