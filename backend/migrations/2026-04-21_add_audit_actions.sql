-- Audit trail per azioni sensibili (POST/PUT/DELETE su risorse business).
--
-- Motivazione:
--   ISO 27001 A.12.4.1 + A.18.1.3 richiedono log di ogni azione sensibile
--   con user, target, outcome e timestamp. Il log applicativo `plenora.logs`
--   copre eventi tecnici ma non struttura l'audit trail per-risorsa. Questa
--   tabella e' alimentata dal decorator `@audit_action` in
--   `backend/app/shared/audit_action.py` e serve per ricerche forensi
--   ("cosa ha fatto user X", "chi ha cancellato la risorsa Y", retention).
--
-- Scelte di schema (strette, no over-design):
--   - id BIGSERIAL: tabella in scrittura pesante, range int64 necessario
--     per orizzonti multi-anno ad alto volume.
--   - timestamp TIMESTAMPTZ NOT NULL DEFAULT now(): standardizzato con
--     il resto del catalogo PLENORA, coerente con app/utils/datetime_utc.
--   - user_id TEXT (nullable): azioni di sistema (scheduled job, login
--     fallito prima della risoluzione utente) sono legittimamente
--     anonime. NULL esplicito e' meglio di un sentinel stringa.
--   - action TEXT NOT NULL: namespace punto-separato, es. 'project.delete'
--     'auth.login', 'datasource.create'. Non enum perche' il dominio si
--     estende nel tempo e un cambio enum richiederebbe migration.
--   - target TEXT (nullable): id della risorsa toccata (project_id,
--     datasource_id, ...). Nullable perche' alcune azioni non hanno
--     target (es. 'auth.login' agisce su se stesso).
--   - status INTEGER NOT NULL: HTTP status code finale osservato.
--     Include 200 (ok), 4xx (policy violation), 500 (crash). Integer
--     non smallint perche' non si guadagnano piu' di 2 byte su una
--     tabella con campi TEXT dominanti.
--   - ip_address TEXT (nullable): client IP quando disponibile (dal
--     request.client.host). Nullable perche' task di background non
--     hanno IP. Non INET per evitare errori di parsing su header
--     proxy malformati che bloccherebbero la write fail-open.
--   - error_message TEXT (nullable): detail dell'HTTPException o
--     "ExceptionType: message" per crash. Troncato a 500 char dal
--     decorator per evitare payload abuse.
--   - metadata JSONB (nullable): slot esteso per info per-action
--     (es. diff body su update). Riservato al futuro, zero consumer
--     attuali; documentato qui per evitare ALTER TABLE successivi.
--
-- Non ci sono FOREIGN KEY verso users(id) perche':
--   1. gli utenti possono essere cancellati ma l'audit deve sopravvivere
--      (regola ACN/GDPR: log di access retention > lifecycle utente).
--   2. user_id copre azioni di sistema con NULL, dove una FK darebbe
--      errore di vincolo.
--
-- Indici giustificati (no over-indexing):
--   - idx_audit_actions_user_ts: query di forensics per-utente ordinate
--     nel tempo ("cosa ha fatto user X nelle ultime 24h").
--   - idx_audit_actions_action: audit review per tipo di azione
--     ("chi ha fatto project.delete nel trimestre Q2").
--   - idx_audit_actions_ts: range temporale full-table per retention
--     cleanup e timeline site-wide.
--
-- Idempotente: IF NOT EXISTS ovunque, re-run safe in dev.

CREATE TABLE IF NOT EXISTS plenora.audit_actions (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id         TEXT,
    action          TEXT NOT NULL,
    target          TEXT,
    status          INTEGER NOT NULL,
    ip_address      TEXT,
    error_message   TEXT,
    metadata        JSONB
);

CREATE INDEX IF NOT EXISTS idx_audit_actions_user_ts
    ON plenora.audit_actions (user_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_audit_actions_action
    ON plenora.audit_actions (action);

CREATE INDEX IF NOT EXISTS idx_audit_actions_ts
    ON plenora.audit_actions (timestamp DESC);
