-- Plenora 2.0 (Fase 4): rate-limiter enrichment (GCRA) su PostgreSQL.
--
-- Rimpiazza la Lua GCRA su Redis. Lo stato GCRA per-scope e' un singolo
-- numero `tat_ms` (theoretical arrival time). L'acquire e' un upsert atomico
-- (un solo statement) che calcola in SQL:
--   effective_tat = GREATEST(tat_ms, now_ms)
--   wait_ms       = effective_tat - now_ms
--   tat_ms        = effective_tat + interval_ms
-- Non passa dal KeyValueStore: serve il read-modify-write atomico (get/set non
-- lo garantiscono), qui ottenuto con l'ON CONFLICT ... GREATEST(...).
--
-- expires_at per lo sweep di manutenzione; la correttezza non dipende dalla
-- pulizia (GREATEST(tat, now) gestisce comunque un tat stantio).

CREATE TABLE IF NOT EXISTS plenora.rate_limit_gcra (
  key        TEXT PRIMARY KEY,
  tat_ms     BIGINT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_gcra_expires_at
  ON plenora.rate_limit_gcra (expires_at);
