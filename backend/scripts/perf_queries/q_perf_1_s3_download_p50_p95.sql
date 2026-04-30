-- Q-PERF-1: p50/p95 latenza s3_download (op=get_bytes)
--
-- Scopo: percentili di latenza del fetch oggetti S3 emesso da
--   backend/app/db/results_db_service.py (marker [PERF] s3_download).
--
-- Sorgente marker: app/db/results_db_service.py, line ~106 (op=get_bytes).
-- Origine: orchestrate/phase_0_consolidation/performance_baseline_0_5f.md §5.1
--
-- Finestra: ultimi 7 giorni (modificare INTERVAL per range diversi).
-- Read-only: SELECT puro su `logs.details` JSONB.

SELECT
    percentile_disc(0.5)  WITHIN GROUP (ORDER BY (details->>'duration_ms')::float) AS p50_ms,
    percentile_disc(0.95) WITHIN GROUP (ORDER BY (details->>'duration_ms')::float) AS p95_ms,
    percentile_disc(0.99) WITHIN GROUP (ORDER BY (details->>'duration_ms')::float) AS p99_ms,
    AVG((details->>'duration_ms')::float)                                          AS avg_ms,
    MIN((details->>'duration_ms')::float)                                          AS min_ms,
    MAX((details->>'duration_ms')::float)                                          AS max_ms,
    COUNT(*)                                                                       AS sample_count
FROM logs
WHERE
    details->>'op' = 'get_bytes'
    AND details ? 'duration_ms'
    AND ts >= NOW() - INTERVAL '7 days'
;

-- Variante con breakdown per dimensione oggetto (size_bytes bucket):
-- SELECT
--     CASE
--         WHEN (details->>'size_bytes')::bigint < 1024*1024        THEN 'small (<1MB)'
--         WHEN (details->>'size_bytes')::bigint < 10*1024*1024     THEN 'medium (1-10MB)'
--         WHEN (details->>'size_bytes')::bigint < 100*1024*1024    THEN 'large (10-100MB)'
--         ELSE 'huge (>=100MB)'
--     END AS size_bucket,
--     percentile_disc(0.5)  WITHIN GROUP (ORDER BY (details->>'duration_ms')::float) AS p50_ms,
--     percentile_disc(0.95) WITHIN GROUP (ORDER BY (details->>'duration_ms')::float) AS p95_ms,
--     COUNT(*) AS sample_count
-- FROM logs
-- WHERE details->>'op' = 'get_bytes'
--   AND details ? 'duration_ms'
--   AND details ? 'size_bytes'
--   AND ts >= NOW() - INTERVAL '7 days'
-- GROUP BY size_bucket
-- ORDER BY MIN((details->>'size_bytes')::bigint);
