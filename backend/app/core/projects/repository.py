"""Narrow project persistence contract used by ProjectService."""

from __future__ import annotations

from typing import Protocol

from app.core.projects import db as project_db


class ProjectCanvasKeyRepository(Protocol):
    """Persistence needed for atomic updates to a single project canvas key."""

    def update_canvas_key(
        self,
        project_id: str,
        key: str,
        value: object,
    ) -> bool:
        """Atomically merge one key into the persisted project canvas."""


class DbProjectCanvasKeyRepository:
    """Default adapter over the domain-owned projects DB bridge."""

    def update_canvas_key(
        self,
        project_id: str,
        key: str,
        value: object,
    ) -> bool:
        return project_db.update_project_canvas_key_atomic(project_id, key, value)
