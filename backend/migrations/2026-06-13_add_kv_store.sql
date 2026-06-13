-- Plenora 2.0 (Fase 4): backend PostgreSQL del KeyValueStore condiviso.
--
-- Rimpiazza Redis come storage transitorio TTL per i consumer KV (blacklist
-- JWT, cache/lock token OAuth). Davanti, in produzione, una cache in-memory
-- per le hot path (blacklist letta a ogni richiesta) — PG e' la verita'.
-- I rate limiter NON passano da qui (port algoritmici propri).
--
-- expires_at NULL = nessuna scadenza; uno sweep di manutenzione cancella le
-- righe scadute (le righe restano leggibili-come-assenti via il filtro nel
-- frattempo).

CREATE TABLE IF NOT EXISTS plenora.kv_store (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  expires_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kv_store_expires_at
  ON plenora.kv_store (expires_at);
