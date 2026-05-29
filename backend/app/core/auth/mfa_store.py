"""DB boundary for MFA state and replay protection."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from app.db.pg_connection import unit_of_work


def register_used_code(
    *,
    user_id: str,
    code_hash: str,
    expires_at: datetime,
) -> bool:
    with unit_of_work() as session:
        row = session.fetchone(
            """
            INSERT INTO user_mfa_used_codes
                (user_id, code_hash, expires_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, code_hash) DO UPDATE
                SET expires_at = EXCLUDED.expires_at
                WHERE user_mfa_used_codes.expires_at <= NOW()
            RETURNING 1
            """,
            (user_id, code_hash, expires_at),
        )
    return row is not None


def vacuum_expired_used_codes() -> int:
    with unit_of_work() as session:
        return session.execute(
            "DELETE FROM user_mfa_used_codes WHERE expires_at <= NOW()"
        ) or 0


def row_for(user_id: str) -> Mapping[str, object] | None:
    with unit_of_work() as session:
        return session.fetchone(
            "SELECT user_id, secret_encrypted, enabled, enrolled_at, last_verified_at "
            "FROM user_mfa WHERE user_id = %s",
            (user_id,),
        )


def save_pending_enrollment(user_id: str, secret_encrypted: str) -> None:
    with unit_of_work() as session:
        session.execute(
            """
            INSERT INTO user_mfa (user_id, secret_encrypted, enabled, created_at, updated_at)
            VALUES (%s, %s, FALSE, NOW(), NOW())
            ON CONFLICT (user_id) DO UPDATE
            SET secret_encrypted = EXCLUDED.secret_encrypted,
                enabled = FALSE,
                enrolled_at = NULL,
                updated_at = NOW()
            """,
            (user_id, secret_encrypted),
        )


def touch_last_verified(user_id: str) -> None:
    with unit_of_work() as session:
        session.execute(
            "UPDATE user_mfa SET last_verified_at = NOW(), updated_at = NOW() "
            "WHERE user_id = %s",
            (user_id,),
        )


def activate_enrollment(user_id: str) -> None:
    with unit_of_work() as session:
        session.execute(
            "UPDATE user_mfa "
            "SET enabled = TRUE, enrolled_at = COALESCE(enrolled_at, NOW()), updated_at = NOW() "
            "WHERE user_id = %s",
            (user_id,),
        )


def delete_enrollment(user_id: str) -> None:
    with unit_of_work() as session:
        session.execute(
            "DELETE FROM user_mfa WHERE user_id = %s",
            (user_id,),
        )
