-- Plenora E2E — seed utente test.
--
-- Inserisce un utente `tester@plenora.test` con password `plenora_test_pwd`
-- nel DB applicativo plenora. Usato dalle fixture pytest del runner.
--
-- ATTENZIONE: lo schema delle tabelle (`users`, `groups`, `user_roles`)
-- dipende da quello reale di plenora. Verificare in
-- `app/core/auth/db.py` o in una migrazione recente PRIMA di applicare.
--
-- Esecuzione:
--   docker exec -i plenora-postgres-1 psql -U plenora -d plenora \
--     < test-e2e/scripts/seed_test_user.sql
--
-- Idempotente: ON CONFLICT DO NOTHING ovunque possibile.

BEGIN;

-- ────────────────────────────────────────────────────────────────────
-- 1. Gruppo di test
-- ────────────────────────────────────────────────────────────────────

INSERT INTO groups (id, name, created_at)
VALUES (
    'e2e-test-group',
    'E2E Test Group',
    NOW()
)
ON CONFLICT (id) DO NOTHING;

-- ────────────────────────────────────────────────────────────────────
-- 2. Utente test
-- ────────────────────────────────────────────────────────────────────
--
-- L'hash della password `plenora_test_pwd` va calcolato con la stessa
-- funzione hash di plenora (bcrypt o argon2 — vedi
-- `app/shared/password_hashing.py`).
--
-- Per ottenerlo:
--   docker exec -it plenora-backend-1 python -c \
--     "from app.shared.password_hashing import hash_password; \
--      print(hash_password('plenora_test_pwd'))"
--
-- Sostituisci `<HASH_QUI>` sotto con l'output.

INSERT INTO users (
    id,
    email,
    username,
    password_hash,
    group_id,
    role,
    mfa_enabled,
    is_active,
    created_at
)
VALUES (
    'e2e-test-user',
    'tester@plenora.test',
    'tester',
    '<HASH_QUI>',  -- <-- SOSTITUIRE col bcrypt/argon2 hash di `plenora_test_pwd`
    'e2e-test-group',
    'user',                 -- ruolo: user (smoke) | admin | superadmin
    FALSE,                  -- MFA disabilitato per i test smoke
    TRUE,
    NOW()
)
ON CONFLICT (email) DO NOTHING;

COMMIT;

-- ────────────────────────────────────────────────────────────────────
-- Verifica
-- ────────────────────────────────────────────────────────────────────

SELECT
    u.id,
    u.email,
    u.username,
    u.role,
    u.is_active,
    u.mfa_enabled,
    g.name AS group_name
FROM users u
LEFT JOIN groups g ON g.id = u.group_id
WHERE u.email = 'tester@plenora.test';
