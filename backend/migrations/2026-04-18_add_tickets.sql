-- Ticketing system — tech_debt + user_support unificati.
-- Audience: superadmin (tech_debt cross-group) + utenti (user_support del proprio gruppo).
--
-- Design decisions:
--   - Unica tabella `tickets` con colonna `kind` per distinguere:
--     'tech_debt'    → debito tecnico, solo admin/superadmin crea e vede
--     'user_support' → segnalazioni utente, qualsiasi auth crea (no esecutore)
--   - Stati pensati per flusso user_support: `waiting_user` permette al
--     support di "rimbalzare" ticket in attesa di info aggiuntive; il
--     tech_debt normalmente non usa quello stato (open → in_progress → resolved).
--   - `assignee` nullable: ticket non ancora triagiato / assegnato ha NULL.
--   - `resolved_at` redundant rispetto a `status='resolved'` ma utile per
--     metriche (time-to-resolution) e per audit storico (un ticket può
--     essere riaperto, ma `resolved_at` conserva il timestamp originale
--     della prima risoluzione).
--
-- Idempotente: IF NOT EXISTS ovunque.

CREATE TABLE IF NOT EXISTS plenora.tickets (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('tech_debt', 'user_support')),
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    severity      TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status        TEXT NOT NULL CHECK (status IN ('open', 'in_progress', 'waiting_user', 'resolved', 'closed', 'reopened')),
    -- Category: enum applicativo, non DB-level CHECK per permettere estensioni
    -- senza migration. Whitelist nel backend (tickets/service.py::TICKET_CATEGORIES).
    category      TEXT NOT NULL DEFAULT 'other',
    created_by    TEXT NOT NULL REFERENCES plenora.users(id),
    assignee      TEXT REFERENCES plenora.users(id),
    -- group_id: per tech_debt è il gruppo di created_by (cross-group visible
    -- solo superadmin). Per user_support è il gruppo di chi ha aperto il
    -- ticket; l'admin del gruppo vede/gestisce solo i suoi.
    group_id      TEXT NOT NULL REFERENCES plenora.groups(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at   TIMESTAMPTZ
);

-- Query tipiche: "ticket aperti del mio gruppo", "ticket assegnati a me",
-- "badge count open+in_progress". Copriamo con indici compositi.
CREATE INDEX IF NOT EXISTS idx_tickets_group_status
    ON plenora.tickets(group_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_assignee_status
    ON plenora.tickets(assignee, status) WHERE assignee IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_created_by
    ON plenora.tickets(created_by);
CREATE INDEX IF NOT EXISTS idx_tickets_kind_status
    ON plenora.tickets(kind, status);
CREATE INDEX IF NOT EXISTS idx_tickets_updated_at
    ON plenora.tickets(updated_at DESC);


-- Commenti bidirezionali (utente ↔ admin).
-- author_id è l'autore del commento (può essere creator del ticket o admin).
-- No state machine qui: l'ordine è puramente cronologico.
CREATE TABLE IF NOT EXISTS plenora.ticket_comments (
    id          TEXT PRIMARY KEY,
    ticket_id   TEXT NOT NULL REFERENCES plenora.tickets(id) ON DELETE CASCADE,
    author_id   TEXT NOT NULL REFERENCES plenora.users(id),
    body        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ticket_comments_ticket_id
    ON plenora.ticket_comments(ticket_id, created_at);


-- Allegati: link a `file_sources` esistenti (riusiamo storage + dispatch
-- DB/S3 threshold gia' implementato). Mime allowlist enforced lato
-- applicativo (solo immagini + log), non a livello DB.
CREATE TABLE IF NOT EXISTS plenora.ticket_attachments (
    id              TEXT PRIMARY KEY,
    ticket_id       TEXT NOT NULL REFERENCES plenora.tickets(id) ON DELETE CASCADE,
    file_source_id  TEXT NOT NULL REFERENCES plenora.file_sources(id) ON DELETE CASCADE,
    uploaded_by     TEXT NOT NULL REFERENCES plenora.users(id),
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ticket_attachments_ticket_id
    ON plenora.ticket_attachments(ticket_id);
