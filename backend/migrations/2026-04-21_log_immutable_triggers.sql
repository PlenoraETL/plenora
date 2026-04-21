-- Append-only trigger per tabelle log / audit (ISO 27001 A.12.4.1).
--
-- Motivazione:
--   A.12.4.1 richiede che gli eventi di sicurezza siano registrati e
--   protetti da manipolazione o eliminazione non autorizzata. PG fornisce
--   GRANT REVOKE ma un role con INSERT privilege implica anche la
--   possibilita' di UPDATE/DELETE via ruoli ereditati o via ownership
--   della riga. Il trigger BEFORE UPDATE/DELETE e' una seconda linea di
--   difesa a livello applicativo: anche se il role applicativo avesse
--   UPDATE/DELETE (di fatto li ha nel pool corrente), ogni tentativo
--   viene rifiutato dal DB stesso.
--
-- Tabelle protette (perimetro stretto, solo quelle dichiarate):
--   - plenora.logs           — log applicativo strutturato
--   - plenora.audit_actions  — audit trail endpoint sensibili (@audit_action)
--
-- Contratto dei trigger:
--   1. UPDATE → SEMPRE `RAISE EXCEPTION`. Nessun bypass: i log/audit non
--      vengono mai modificati in place. Una correzione si fa con una
--      nuova INSERT (nuova riga), non via UPDATE.
--
--   2. DELETE senza bypass → `RAISE EXCEPTION`. Default deny: il ramo
--      normale dell'applicazione NON cancella log/audit.
--
--   3. DELETE con bypass → consentito. Bypass = session variable
--      `plenora.retention_cleanup = 'true'`. Un job di retention
--      autorizzato dichiara esplicitamente il suo scope con
--      `SET LOCAL plenora.retention_cleanup = 'true'` dentro la
--      transazione del DELETE. `SET LOCAL` limita l'effetto alla
--      transazione, quindi il flag non leakerebbe oltre.
--
--   Nota sul SET LOCAL: il trigger accetta qualsiasi GUC settata alla
--   stringa 'true' (SET o SET LOCAL). Il contratto applicativo richiede
--   SET LOCAL ma il DB non lo puo' enforcare. La convenzione e' esplicita
--   nei call-site (beat_scheduler retention cleanup) e nei test.
--
-- Design della funzione trigger:
--   Una singola funzione `plenora.block_log_modifications()` serve sia
--   `plenora.logs` sia `plenora.audit_actions`, usando TG_TABLE_NAME per
--   il messaggio d'errore. Una sola funzione = un solo point-of-truth
--   per il contratto (eventuali evoluzioni future della policy, es.
--   logging supplementare sul tentativo bloccato, andrebbero in un
--   solo posto).
--
-- Idempotente:
--   CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS + CREATE
--   TRIGGER → re-run safe in dev/staging.
--
-- Non c'e' logica laterale: nessuna INSERT ausiliaria, nessun side
-- effect oltre il RAISE. Deliberatamente minimal per auditability.

CREATE OR REPLACE FUNCTION plenora.block_log_modifications()
RETURNS TRIGGER AS $$
DECLARE
    retention_flag TEXT;
BEGIN
    -- UPDATE: mai ammesso. Le righe di log/audit sono immutabili: per
    -- correggere un log si fa una nuova INSERT con un marker di
    -- superseding, non si muta la riga originale.
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'Tabella %.% e'' append-only: UPDATE vietato (policy ISO 27001 A.12.4.1)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- DELETE: ammesso SOLO con bypass esplicito di retention cleanup.
    -- current_setting(key, missing_ok=true) ritorna NULL se la GUC non
    -- e' stata settata nella tx corrente (niente exception da catch).
    IF TG_OP = 'DELETE' THEN
        retention_flag := current_setting('plenora.retention_cleanup', true);
        IF retention_flag = 'true' THEN
            RETURN OLD;  -- consentito: retention job autorizzato
        END IF;
        RAISE EXCEPTION
            'Tabella %.% e'' append-only: DELETE richiede SET LOCAL plenora.retention_cleanup = ''true'' (retention job autorizzato)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    RETURN OLD;  -- ramo TG_OP teoricamente non raggiungibile (solo UPDATE/DELETE)
END;
$$ LANGUAGE plpgsql;


-- ---------------------------------------------------------------------------
-- plenora.logs
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_logs_block_update ON plenora.logs;
CREATE TRIGGER trg_logs_block_update
    BEFORE UPDATE ON plenora.logs
    FOR EACH ROW
    EXECUTE FUNCTION plenora.block_log_modifications();

DROP TRIGGER IF EXISTS trg_logs_block_delete ON plenora.logs;
CREATE TRIGGER trg_logs_block_delete
    BEFORE DELETE ON plenora.logs
    FOR EACH ROW
    EXECUTE FUNCTION plenora.block_log_modifications();


-- ---------------------------------------------------------------------------
-- plenora.audit_actions
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_audit_actions_block_update ON plenora.audit_actions;
CREATE TRIGGER trg_audit_actions_block_update
    BEFORE UPDATE ON plenora.audit_actions
    FOR EACH ROW
    EXECUTE FUNCTION plenora.block_log_modifications();

DROP TRIGGER IF EXISTS trg_audit_actions_block_delete ON plenora.audit_actions;
CREATE TRIGGER trg_audit_actions_block_delete
    BEFORE DELETE ON plenora.audit_actions
    FOR EACH ROW
    EXECUTE FUNCTION plenora.block_log_modifications();
