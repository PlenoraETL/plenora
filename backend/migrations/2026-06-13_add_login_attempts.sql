-- Plenora 2.0 (Fase 4): rate-limiter login su PostgreSQL (sliding window).
--
-- Rimpiazza la sliding window Redis (ZSET per IP). Ogni tentativo di login
-- fallito = una riga (ip, attempt_at); il check conta le righe nella finestra.
-- Non passa dal KeyValueStore: e' un port algoritmico proprio (la primitiva
-- sorted-set non esiste su PG, qui si usa count su timestamp indicizzati).
--
-- Le righe vecchie vengono ripulite opportunisticamente a ogni record + da
-- uno sweep di manutenzione; il check ignora comunque quelle fuori finestra.

CREATE TABLE IF NOT EXISTS plenora.login_attempts (
  ip         TEXT NOT NULL,
  attempt_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_at
  ON plenora.login_attempts (ip, attempt_at);
