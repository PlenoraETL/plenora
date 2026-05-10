-- Plenora E2E test dataset — volume Oracle
-- Genera 10000 righe in events_log per stress test.
--
-- Distribuzione deterministica (no random):
--   customer_id  = MOD(id-1, 12) + 1                 (cycle 1..12, FK valida)
--   event_type   = MOD(id, 4)                         (view/click/purchase/logout)
--   occurred_at  = TIMESTAMP '2024-01-01' + MOD(id, 365) giorni
--   value        = NULL ogni 5° riga, altrimenti id * 0.10

INSERT INTO events_log (id, customer_id, event_type, occurred_at, value)
SELECT
    LEVEL AS id,
    MOD(LEVEL - 1, 12) + 1 AS customer_id,
    CASE MOD(LEVEL, 4)
        WHEN 0 THEN 'view'
        WHEN 1 THEN 'click'
        WHEN 2 THEN 'purchase'
        ELSE        'logout'
    END AS event_type,
    TIMESTAMP '2024-01-01 00:00:00' + NUMTODSINTERVAL(MOD(LEVEL, 365), 'DAY') AS occurred_at,
    CASE WHEN MOD(LEVEL, 5) = 0 THEN NULL
         ELSE LEVEL * 0.10
    END AS value
FROM dual
CONNECT BY LEVEL <= 10000;

COMMIT;
