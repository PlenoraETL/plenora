"""
Users DAL — estratto da `config_db_service.py` (scope micro Batch G #9 settima

riapertura — governance autonoma auth-critical, invariants §6 commit `16bebfa`).
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


def list_all_users() -> List[Dict]:
    """Lista TUTTI gli utenti (no filtro per gruppo).

    Usato esclusivamente dal superadmin (vedi
    `app/core/auth/users_router.py:list_users`). Single-query (no N+1
    per gruppo); il caller filtra/sanifica i campi prima di
    serializzare. La SELECT replica `SELECT * FROM users ORDER BY
    username` cosi' la shape della riga combacia byte-per-byte con
    quella che il router pre-DB-ENGINE-006 leggeva via
    `_cfg_db.fetchall`.
    """
    return fetchall("SELECT * FROM users ORDER BY username")


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
