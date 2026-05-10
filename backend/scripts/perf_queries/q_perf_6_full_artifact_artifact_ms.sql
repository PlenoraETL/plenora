-- Q-PERF-6: full_artifact artifact_ms distribution
--
-- Scopo: latenza scritture full_artifact (parquet/...). Filtro su
--   `artifact_status = 'complete'` per escludere casi parziali.
--
-- Sorgente marker: app/core/intermediate/snapshot.py / artifacts/resolver.py,
--   marker [PERF] persistence_mode=full_artifact con campi `artifact_ms`,
--   `artifact_status`.
--
-- Finestra: ultimi 7 giorni (modificare INTERVAL per range diversi).
-- Read-only: SELECT puro su `logs.details` JSONB.

SELECT
    details->>'persistence_mode'                  AS persistence_mode,
    details->>'artifact_status'                   AS artifact_status,
    COUNT(*)                                      AS sample_count,
    AVG((details->>'artifact_ms')::float)         AS avg_ms,
    percentile_disc(0.5)  WITHIN GROUP (
        ORDER BY (details->>'artifact_ms')::float
    )                                             AS p50_ms,
    percentile_disc(0.95) WITHIN GROUP (
        ORDER BY (details->>'artifact_ms')::float
    )                                             AS p95_ms,
    MIN((details->>'artifact_ms')::float)         AS min_ms,
    MAX((details->>'artifact_ms')::float)         AS max_ms
FROM logs
WHERE
    details->>'persistence_mode' = 'full_artifact'
    AND details->>'artifact_status' = 'complete'
    AND details ? 'artifact_ms'
    AND ts >= NOW() - INTERVAL '7 days'
GROUP BY
    details->>'persistence_mode',
    details->>'artifact_status'
;

-- Variante: top 10 run per artifact_ms (slow path drill-down):
-- SELECT
--     ts,
--     details->>'run_id'                  AS run_id,
--     details->>'project_id'              AS project_id,
--     (details->>'artifact_ms')::float    AS artifact_ms,
--     (details->>'rows_count')::int       AS rows_count,
--     details->>'artifact_format'         AS artifact_format
-- FROM logs
-- WHERE details->>'persistence_mode' = 'full_artifact'
--   AND details->>'artifact_status' = 'complete'
--   AND details ? 'artifact_ms'
--   AND ts >= NOW() - INTERVAL '7 days'
-- ORDER BY (details->>'artifact_ms')::float DESC
-- LIMIT 10;
