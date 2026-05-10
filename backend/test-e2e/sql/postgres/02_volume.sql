-- Plenora E2E test dataset — volume PostgreSQL
-- Genera 10000 righe in events_log per stress test:
--   * paginazione, fingerprint sample, preview limit
--   * cross_join_oom_guard, performance loader
-- Distribuzione deterministica (no random):
--   customer_id  = ((id-1) % 12) + 1                 (cycle 1..12, FK valida)
--   event_type   = id % 4                            (view/click/purchase/logout)
--   occurred_at  = '2024-01-01' + (id MOD 365) days
--   value        = NULL ogni 5° riga, altrimenti id * 0.10

INSERT INTO events_log (id, customer_id, event_type, occurred_at, value)
SELECT
    i AS id,
    ((i - 1) % 12) + 1 AS customer_id,
    CASE (i % 4)
        WHEN 0 THEN 'view'
        WHEN 1 THEN 'click'
        WHEN 2 THEN 'purchase'
        ELSE        'logout'
    END AS event_type,
    TIMESTAMP '2024-01-01 00:00:00' + ((i % 365) * INTERVAL '1 day') AS occurred_at,
    CASE WHEN (i % 5) = 0 THEN NULL
         ELSE (i * 0.10)::DECIMAL(12, 2)
    END AS value
FROM generate_series(1, 10000) AS s(i);
