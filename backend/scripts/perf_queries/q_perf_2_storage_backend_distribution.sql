-- Q-PERF-2: distribuzione fetch_blob per backend (object_storage vs db_legacy)
--
-- Scopo: split dei fetch result tra storage modes, count + media latenza.
--   Utile per verificare che il default `object_storage` sia effettivamente
--   in uso e per detectare regressioni verso `db_legacy`.
--
-- Sorgente marker: app/db/results_db_service.py, line ~149 (op=fetch_blob).
--
-- Finestra: ultimi 7 giorni (modificare INTERVAL per range diversi).
-- Read-only: SELECT puro su `logs.details` JSONB.

SELECT
    details->>'backend'                                AS backend,
    COUNT(*)                                           AS fetch_count,
    AVG((details->>'duration_ms')::float)              AS avg_duration_ms,
    percentile_disc(0.5)  WITHIN GROUP (
        ORDER BY (details->>'duration_ms')::float
    )                                                  AS p50_ms,
    percentile_disc(0.95) WITHIN GROUP (
        ORDER BY (details->>'duration_ms')::float
    )                                                  AS p95_ms,
    MIN((details->>'duration_ms')::float)              AS min_ms,
    MAX((details->>'duration_ms')::float)              AS max_ms
FROM logs
WHERE
    details->>'op' = 'fetch_blob'
    AND details ? 'backend'
    AND details ? 'duration_ms'
    AND ts >= NOW() - INTERVAL '7 days'
GROUP BY details->>'backend'
ORDER BY fetch_count DESC
;

-- Variante timeline giornaliera (per detectare drift cross-week):
-- SELECT
--     DATE_TRUNC('day', ts) AS day,
--     details->>'backend'   AS backend,
--     COUNT(*)              AS fetch_count
-- FROM logs
-- WHERE details->>'op' = 'fetch_blob'
--   AND details ? 'backend'
--   AND ts >= NOW() - INTERVAL '30 days'
-- GROUP BY day, details->>'backend'
-- ORDER BY day DESC, backend;
