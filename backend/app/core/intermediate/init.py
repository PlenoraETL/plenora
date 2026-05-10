"""Intermediate storage initialization boundary."""

from __future__ import annotations

from app.utils.logger import get_logger


logger = get_logger(__name__)


def init_db() -> None:
    """Initialize intermediate storage.

    PostgreSQL tables are owned by migrations, so runtime startup only needs a
    stable explicit hook.
    """
    logger.info("Intermediate DB: PostgreSQL tables already present", op="init_db")


__all__ = ["init_db"]
