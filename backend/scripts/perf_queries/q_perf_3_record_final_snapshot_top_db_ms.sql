-- Q-PERF-3: top 20 record_final_snapshot per db_ms (slow path detection)
--
-- Scopo: identificare le snapshot writes piu' lente per intervenire
--   chirurgicamente (single hot run / hot project) prima di refactor
--   strutturale.
--
-- Sorgente marker: app/core/intermediate/snapshot.py, marker [PERF]
--   record_final_snapshot con campo `db_ms`.
--
-- Finestra: ultimi 7 giorni (modificare INTERVAL per range diversi).
-- Read-only: SELECT puro su `logs.details` JSONB.

SELECT
    ts,
    details->>'run_id'              AS run_id,
    details->>'project_id'          AS project_id,
    details->>'persistence_mode'    AS persistence_mode,
    (details->>'db_ms')::float      AS db_ms,
    (details->>'rows_count')::int   AS rows_count,
    details->>'op'                  AS op
FROM logs
WHERE
    details->>'marker' = 'PERF'
    AND details->>'op' = 'record_final_snapshot'
    AND details ? 'db_ms'
    AND ts >= NOW() - INTERVAL '7 days'
ORDER BY (details->>'db_ms')::float DESC
LIMIT 20
;

-- Variante: aggregato per project_id (median + count, identifica
-- progetti consistentemente lenti):
-- SELECT
--     details->>'project_id' AS project_id,
--     COUNT(*) AS snapshot_count,
--     percentile_disc(0.5) WITHIN GROUP (
--         ORDER BY (details->>'db_ms')::float
--     ) AS median_db_ms,
--     MAX((details->>'db_ms')::float) AS max_db_ms
-- FROM logs
-- WHERE details->>'marker' = 'PERF'
--   AND details->>'op' = 'record_final_snapshot'
--   AND details ? 'db_ms'
--   AND details ? 'project_id'
--   AND ts >= NOW() - INTERVAL '30 days'
-- GROUP BY details->>'project_id'
-- HAVING COUNT(*) >= 3
-- ORDER BY median_db_ms DESC
-- LIMIT 20;
