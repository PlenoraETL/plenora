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

INSERT INTO plenora.groups (id, name, description)
VALUES (
    'e2e-test-group',
    'E2E Test Group',
    'Gruppo isolato per i test end-to-end'
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

-- Schema reale (vedi `\d plenora.users`):
--   id text PRIMARY KEY, username text UNIQUE NOT NULL,
--   password_hash text NOT NULL, email text NULL,
--   group_id text NOT NULL FK groups, role text DEFAULT 'user',
--   is_active boolean DEFAULT true,
--   created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now()
-- NB: niente colonna `mfa_enabled` (rimossa); UNIQUE su `username`, NON su `email`.
INSERT INTO plenora.users (
    id,
    email,
    username,
    password_hash,
    group_id,
    role,
    is_active
)
VALUES (
    'e2e-test-user',
    'tester@plenora.test',
    'tester',
    '<HASH_QUI>',  -- <-- SOSTITUITO a runtime con argon2 hash di `plenora_test_pwd`
    'e2e-test-group',
    'user',                 -- ruolo: user (smoke) | admin | superadmin
    TRUE
)
ON CONFLICT (username) DO UPDATE SET
    password_hash = EXCLUDED.password_hash,
    role = EXCLUDED.role,
    is_active = EXCLUDED.is_active;

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
    g.name AS group_name
FROM plenora.users u
LEFT JOIN plenora.groups g ON g.id = u.group_id
WHERE u.username = 'tester';
