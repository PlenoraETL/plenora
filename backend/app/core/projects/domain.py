"""Pure project-domain policies used by ProjectService."""

from __future__ import annotations

from datetime import datetime
from typing import Optional


def build_project_id(name: str, generated_at: datetime, unique_token: str) -> str:
    """Return a readable project id for a project name."""
    timestamp = generated_at.strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_")).strip()
    safe_name = safe_name.replace(" ", "_")[:30]
    return f"{safe_name}_{timestamp}_{unique_token[:8]}"


def resolve_project_group_id(group_id: Optional[str], default_group: str) -> str:
    """Return the caller group when present, otherwise the configured default."""
    return group_id or default_group
