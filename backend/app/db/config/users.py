"""
Users DAL — estratto da `config_db_service.py` (scope micro Batch G #9 settima
riapertura — governance autonoma auth-critical, invariants §6 commit `16bebfa`).

Cluster `users` (7 funzioni): CRUD sulla tabella `users` (root RBAC PLENORA —
login/JWT/password Argon2id/role/is_active/group_id). Helper `_safe_set_clause`
importato lazy dal facade per rompere ciclo `config_db_service` ↔ `config/users`
e preservare zero-call-site-touched sugli altri 4 cluster che lo condividono
(projects/schedules + datasources/connections via lazy).

Note auth-critical (ratified ex-ante invariants §6 settima riapertura): questo
modulo è patch-target di 5 suite security unit via `auth_mod.db.*` (facade
chain preservata) + 1 patch diretto `app.db.config_db_service.get_user` in
`test_n9_async_permission_revoked_contract.py:210` (re-export come attributo
modulo preserva identità per monkeypatch). Consumer non-facade:
`shared/async_user_recheck.py:22` via `from app.db.config_db_service import
get_user` → risolve attributo re-exportato. `core/auth/auth.py` 11 call site
via `import app.db.db_service as db; db.<method>` → chain triplice invariata.
"""

from typing import Dict, List, Optional

from app.db.pg_connection import fetchone, fetchall, execute


def get_user(user_id: str) -> Optional[Dict]:
    return fetchone("SELECT * FROM users WHERE id = ?", (user_id,))


def get_user_by_username(username: str) -> Optional[Dict]:
    return fetchone("SELECT * FROM users WHERE username = ?", (username,))


def list_users(group_id: str) -> List[Dict]:
    return fetchall(
        "SELECT id, username, email, group_id, role, is_active, created_at FROM users WHERE group_id = ? ORDER BY username",
        (group_id,)
    )


def create_user(user_id: str, username: str, password_hash: str, group_id: str,
                role: str = "user", email: str = "") -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO users (id, username, password_hash, email, group_id, role, is_active, created_at, updated_at) VALUES (?,?,?,?,?,?,TRUE,?,?)",
        (user_id, username, password_hash, email, group_id, role, now, now)
    )
    return get_user(user_id)


def update_user(user_id: str, **fields) -> Optional[Dict]:
    from datetime import datetime, timezone
    # Lazy import: `_safe_set_clause` è helper private cross-cluster nel facade
    # `config_db_service` (usato da users/projects/schedules + datasources/
    # connections via lazy). Import locale rompe ciclo facade ↔ config/users
    # senza spostare il helper (violerebbe zero call site touched sugli altri
    # 4 cluster). Vedi invariants §6 commit `16bebfa`.
    from app.db.config_db_service import _safe_set_clause
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, vals = _safe_set_clause("users", fields)
    execute(f"UPDATE users SET {set_clause} WHERE id = ?", vals + (user_id,))
    return get_user(user_id)


def update_user_password(user_id: str, password_hash: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, now, user_id))


def delete_user(user_id: str) -> bool:
    cur = execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cur.rowcount > 0
