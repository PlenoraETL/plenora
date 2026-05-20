-- Plenora E2E test dataset — volume MySQL (≥ 8.0)
-- Genera 10000 righe in events_log per stress test.
-- Richiede cte_max_recursion_depth >= 10000.
--
-- Distribuzione deterministica (no random):
--   customer_id  = ((id-1) % 12) + 1                 (cycle 1..12, FK valida)
--   event_type   = id % 4                            (view/click/purchase/logout)
--   occurred_at  = '2024-01-01' + (id MOD 365) days
--   value        = NULL ogni 5° riga, altrimenti id * 0.10

SET SESSION cte_max_recursion_depth = 10000;

INSERT INTO events_log (id, customer_id, event_type, occurred_at, value)
WITH RECURSIVE seq (i) AS (
    SELECT 1
    UNION ALL
    SELECT i + 1 FROM seq WHERE i < 10000
)
SELECT
    i AS id,
    ((i - 1) MOD 12) + 1 AS customer_id,
    CASE (i MOD 4)
        WHEN 0 THEN 'view'
        WHEN 1 THEN 'click'
        WHEN 2 THEN 'purchase'
        ELSE        'logout'
    END AS event_type,
    DATE_ADD('2024-01-01 00:00:00', INTERVAL (i MOD 365) DAY) AS occurred_at,
    CASE WHEN (i MOD 5) = 0 THEN NULL
         ELSE CAST(i * 0.10 AS DECIMAL(12, 2))
    END AS value
FROM seq;
