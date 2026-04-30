-- Q-PERF-4: load_source_project p95 per project_id
--
-- Scopo: identificare progetti con caricamento sorgente p95 lento.
--   Filtro `HAVING COUNT(*) >= 5` per scartare progetti con campione
--   troppo piccolo (alta varianza).
--
-- Sorgente marker: app/core/arricchisci/api_connection_source_loading.py,
--   marker [PERF] op=load_source_project con campo `duration_ms`.
-- Origine: orchestrate/phase_0_consolidation/performance_baseline_0_5f.md §5.1
--
-- Finestra: ultimi 7 giorni (modificare INTERVAL per range diversi).
-- Read-only: SELECT puro su `logs.details` JSONB.

SELECT
    details->>'project_id'                    AS project_id,
    COUNT(*)                                  AS load_count,
    AVG((details->>'duration_ms')::float)     AS avg_ms,
    percentile_disc(0.5)  WITHIN GROUP (
        ORDER BY (details->>'duration_ms')::float
    )                                         AS p50_ms,
    percentile_disc(0.95) WITHIN GROUP (
        ORDER BY (details->>'duration_ms')::float
    )                                         AS p95_ms,
    MIN((details->>'duration_ms')::float)     AS min_ms,
    MAX((details->>'duration_ms')::float)     AS max_ms
FROM logs
WHERE
    details->>'op' = 'load_source_project'
    AND details ? 'duration_ms'
    AND details ? 'project_id'
    AND ts >= NOW() - INTERVAL '7 days'
GROUP BY details->>'project_id'
HAVING COUNT(*) >= 5
ORDER BY p95_ms DESC
LIMIT 20
;

-- Variante: aggregata per source_project (chi viene letto piu' spesso):
-- SELECT
--     details->>'source_project_id' AS source_project,
--     COUNT(*) AS read_count,
--     AVG((details->>'duration_ms')::float) AS avg_ms
-- FROM logs
-- WHERE details->>'op' = 'load_source_project'
--   AND details ? 'source_project_id'
--   AND details ? 'duration_ms'
--   AND ts >= NOW() - INTERVAL '30 days'
-- GROUP BY details->>'source_project_id'
-- ORDER BY read_count DESC
-- LIMIT 20;
