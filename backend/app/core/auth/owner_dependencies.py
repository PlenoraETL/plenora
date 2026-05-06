"""FastAPI owner dependencies - canonical leaf.

BE-GO-READY-AUTH-ENGINE-018-OWNER-DEPENDENCIES-LEAF.

Three DB-bound FastAPI dependency functions that enforce per-resource
ownership. Extracted from ``app.core.auth.auth`` so routers can import
owner checks without pulling the entire auth facade.

  - ``require_project_owner``   - 404/403 unless caller owns or admins the project;
  - ``require_schedule_owner``  - same for schedules;
  - ``require_file_owner``      - same for file_sources.

Pre-018 these lived inline in ``app.core.auth.auth``. They could not be
moved to ``app.core.auth.ownership`` because that module at the time still
imported from ``auth.py`` (creating an ``auth ↔ ownership`` cycle). That
cycle has been resolved since BE-GO-READY-AUTH-ENGINE-005: ``ownership.py``
now imports ``ROLES_ADMIN_OR_ABOVE`` from the pure leaf ``app.core.auth.roles``
and ``CurrentUser`` from ``app.shared.contracts.auth``. This module is
therefore cycle-free.

Boundary
--------
This leaf MAY import:

  - ``fastapi.Depends`` (declares Depends callables, not routes);
  - ``app.core.auth.dependencies.get_current_user`` (the resolved user;
    canonical leaf, no facade cycle);
  - ``app.core.auth.ownership._check_ownership`` (the shared ownership
    logic; DB-bound, lives in its canonical home);
  - ``app.shared.contracts.auth.CurrentUser`` (typed return);
  - DB config leaves (lazy per-function): ``app.db.config.projects``,
    ``app.db.config.schedules``, ``app.db.config.file_sources``.

This leaf MUST NOT import:

  - ``app.core.auth.auth`` (the facade — would create a cycle);
  - ``app.db.db_service`` (use the canonical DB config leaves instead);
  - ``fastapi.APIRouter`` (this module only declares Depends callables).

KEEP_COMPAT
-----------
``app.core.auth.auth`` re-exports the three symbols from this module via:

    from app.core.auth.owner_dependencies import (
        require_project_owner,
        require_schedule_owner,
        require_file_owner,
    )

so the legacy import shape ``from app.core.auth.auth import
require_project_owner`` keeps working byte-for-byte. Identity is
preserved (``auth.require_project_owner is
owner_dependencies.require_project_owner``) which means FastAPI
``Depends(require_project_owner)`` resolves to the same callable
regardless of which import path the router chose.
"""

from __future__ import annotations

from fastapi import Depends

from app.core.auth.dependencies import get_current_user
from app.core.auth.ownership import _check_ownership
from app.shared.contracts.auth import CurrentUser


__all__ = [
    "require_project_owner",
    "require_schedule_owner",
    "require_file_owner",
]


def require_project_owner(
    project_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Owner check su projects. Da usare per update/delete/canvas/duplicate.

    BE-GO-READY-AUTH-ENGINE-012: lookup migrato al leaf
    ``app.db.config.projects``.
    BE-GO-READY-AUTH-ENGINE-018: estratto da ``auth.py`` al leaf canonico.
    """
    from app.db.config.projects import get_project as _get_project
    project = _get_project(project_id)
    _check_ownership(project, "Progetto", current_user)
    return current_user


def require_schedule_owner(
    schedule_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Owner check su schedules. Da usare per update/delete/toggle/run.

    BE-GO-READY-AUTH-ENGINE-012: lookup migrato al leaf
    ``app.db.config.schedules``.
    BE-GO-READY-AUTH-ENGINE-018: estratto da ``auth.py`` al leaf canonico.
    """
    from app.db.config.schedules import get_schedule as _get_schedule
    schedule = _get_schedule(schedule_id)
    _check_ownership(schedule, "Schedule", current_user)
    return current_user


def require_file_owner(
    file_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Owner check su file_sources. Da usare per delete.

    BE-GO-READY-AUTH-ENGINE-012: lookup migrato al leaf
    ``app.db.config.file_sources``.
    BE-GO-READY-AUTH-ENGINE-018: estratto da ``auth.py`` al leaf canonico.
    """
    from app.db.config.file_sources import get_file_source as _get_file_source
    file_source = _get_file_source(file_id)
    _check_ownership(file_source, "File", current_user)
    return current_user
