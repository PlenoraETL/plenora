-- Soft delete per tickets.
--
-- Rationale: cancellare hard un ticket fa sparire commenti e allegati
-- via FK cascade — comportamento OK per `tech_debt` (rumore tecnico)
-- ma rischioso per `user_support` (cliente puo' chiedere "cosa avevo
-- aperto a marzo?" e non avere risposta).
--
-- Soluzione: aggiungere `deleted_at` nullable. Le query CRUD filtrano
-- WHERE deleted_at IS NULL by default. La hard delete via cascade
-- resta disponibile via `?hard=true` per superadmin (raro, audit log
-- obbligatorio lato applicativo).
--
-- Idempotente: ADD COLUMN IF NOT EXISTS.

ALTER TABLE plenora.tickets
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Indice parziale per filtrare velocemente i non-cancellati nelle query
-- piu' frequenti (list, count). I cancellati restano cercabili via
-- query esplicita (admin recovery / audit) ma sono fuori dal critical path.
CREATE INDEX IF NOT EXISTS idx_tickets_active_only
    ON plenora.tickets(updated_at DESC)
    WHERE deleted_at IS NULL;
