"""Read-only catalog service for runtime object storage."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

import logging

from psycopg import Error as PsycopgError
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.core.projects import db as projects_db
from app.core.results import db as results_db
from app.core.results.orphan_cleanup import find_orphan_runs
from app.core.io.object_storage import get_runtime_object_storage
from app.core.io.tenant_namespace import (
    legacy_domain,
    parse_tenant_key,
    safe_segment,
    tenant_domain_prefix,
    tenant_prefix,
)
from app.shared.resources.storage_layout import TENANT_DOMAINS

logger = logging.getLogger(__name__)


class StorageObjectEntry(BaseModel):
    key: str
    size_bytes: int
    last_modified: datetime
    tenant_group_id: Optional[str] = None
    domain: str
    resource_id: Optional[str] = None
    legacy: bool = False


class StorageObjectPage(BaseModel):
    bucket: str
    scope: Literal["canonical", "legacy"]
    prefix: str
    objects: list[StorageObjectEntry]
    next_cursor: Optional[str] = None


class StorageProjectResult(BaseModel):
    id: str
    key: str
    filename: str
    format: str
    size_bytes: int
    rows_count: Optional[int] = None
    created_at: datetime


class StorageProjectGroup(BaseModel):
    project_id: str
    project_name: str
    project_type: Optional[str] = None
    group_id: str
    results: list[StorageProjectResult]
    # Esecuzioni intermedie di un progetto orfano: anche un orfano SENZA
    # risultati compare nel catalogo, perche' la bonifica lo toccherebbe.
    intermediate_runs: int = 0
    # True se il progetto non esiste piu': lo decide il server per id, non il
    # nome mostrato (audit frontend R10.4).
    orphan: bool = False


class StorageProjectCatalog(BaseModel):
    projects: list[StorageProjectGroup]
    results_count: int
    size_bytes: int
    # Le esecuzioni intermedie non si sono potute leggere (DB intermedio non
    # disponibile): il catalogo dei risultati resta, senza quella parte.
    intermediate_runs_unavailable: bool = False


def _resource_id(domain: str, segments: tuple[str, ...]) -> str | None:
    if not segments:
        return None
    if domain == "intermediate":
        project = next(
            (part.split("=", 1)[1] for part in segments if part.startswith("project=")),
            None,
        )
        return project
    return segments[0]


def _entry(raw: dict) -> StorageObjectEntry | None:
    key = str(raw.get("key") or "")
    parsed = parse_tenant_key(key)
    if parsed is not None:
        return StorageObjectEntry(
            key=key,
            size_bytes=int(raw.get("size") or 0),
            last_modified=raw["last_modified"],
            tenant_group_id=parsed.group_id,
            domain=parsed.domain,
            resource_id=_resource_id(parsed.domain, parsed.relative_segments),
            legacy=False,
        )
    domain = legacy_domain(key)
    if domain is None:
        return None
    segments = tuple(key.split("/")[1:])
    return StorageObjectEntry(
        key=key,
        size_bytes=int(raw.get("size") or 0),
        last_modified=raw["last_modified"],
        domain=domain,
        resource_id=_resource_id(domain, segments),
        legacy=True,
    )


def list_runtime_objects(
    *,
    scope: Literal["canonical", "legacy"] = "canonical",
    group_id: str | None = None,
    domain: str | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> StorageObjectPage:
    if domain is not None:
        domain = safe_segment(domain, label="storage domain")
        if domain not in TENANT_DOMAINS:
            raise ValueError("storage domain non supportato")
    if scope == "canonical":
        if group_id:
            prefix = (
                tenant_domain_prefix(group_id, domain)
                if domain else tenant_prefix(group_id) + "/"
            )
        else:
            prefix = "tenants/"
    else:
        if group_id:
            raise ValueError("le chiavi legacy non contengono group_id")
        prefix = f"{domain}/" if domain else ""

    storage = get_runtime_object_storage()
    page = storage.list_objects_page(
        prefix,
        limit=max(1, min(int(limit), 500)),
        cursor=cursor,
    )
    entries = [entry for raw in page["objects"] if (entry := _entry(raw))]
    if scope == "legacy":
        entries = [entry for entry in entries if entry.legacy]
    elif domain and not group_id:
        entries = [entry for entry in entries if entry.domain == domain]
    return StorageObjectPage(
        bucket=str(getattr(storage, "bucket", "runtime")),
        scope=scope,
        prefix=prefix,
        objects=entries,
        next_cursor=page.get("next_cursor"),
    )


def list_project_results(group_id: str | None = None) -> StorageProjectCatalog:
    """Group every retained result by project, newest first."""

    project_rows = projects_db.list_projects(group_id=group_id)
    projects = {(row.group_id, row.id): row for row in project_rows}
    grouped: dict[tuple[str, str], list[StorageProjectResult]] = {}
    total_size = 0
    for row in results_db.list_admin_result_rows(group_id=group_id):
        if not row.id or not row.project_id or not row.group_id or not row.storage_key:
            continue
        entry = StorageProjectResult(
            id=row.id,
            key=row.storage_key,
            filename=row.filename or f"{row.id}.{row.format or 'bin'}",
            format=row.format or "bin",
            size_bytes=int(row.size_bytes or 0),
            rows_count=row.rows_count,
            created_at=row.created_at,
        )
        grouped.setdefault((row.group_id, row.project_id), []).append(entry)
        total_size += entry.size_bytes

    # Orfani delle esecuzioni intermedie dalla STESSA fonte della bonifica
    # (results.orphan_cleanup.find_orphan_runs): il catalogo mostra tutto cio'
    # che la bonifica globale toccherebbe, anche senza risultati.
    orphan_runs: dict[tuple[str, str], int] = {}
    # Lettura OPZIONALE (come la bonifica, che la tollera): se il DB delle
    # esecuzioni intermedie non risponde, il catalogo dei risultati resta.
    intermediate_runs_unavailable = False
    try:
        orphan_rows = find_orphan_runs()
    except (SQLAlchemyError, PsycopgError, OSError, RuntimeError) as exc:
        logger.warning("Catalogo Object Storage: esecuzioni intermedie non leggibili: %s", exc)
        orphan_rows = []
        intermediate_runs_unavailable = True
    for canvas_id, run_group, runs in orphan_rows:
        if group_id and run_group != group_id:
            continue
        key = (run_group, canvas_id)
        orphan_runs[key] = orphan_runs.get(key, 0) + runs
        grouped.setdefault(key, [])

    catalog: list[StorageProjectGroup] = []
    for (owner_group, project_id), results in grouped.items():
        project = projects.get((owner_group, project_id))
        catalog.append(
            StorageProjectGroup(
                project_id=project_id,
                project_name=(project.name if project else "Progetto eliminato"),
                project_type=(project.project_type if project else None),
                group_id=owner_group,
                results=results,
                intermediate_runs=orphan_runs.get((owner_group, project_id), 0),
                orphan=project is None,
            )
        )
    catalog.sort(
        key=lambda item: item.results[0].created_at if item.results else datetime.min,
        reverse=True,
    )
    return StorageProjectCatalog(
        projects=catalog,
        results_count=sum(len(item.results) for item in catalog),
        size_bytes=total_size,
        intermediate_runs_unavailable=intermediate_runs_unavailable,
    )


def delete_preview_objects(
    *,
    scope: Literal["canonical", "legacy"],
    group_id: str | None = None,
) -> int:
    """Delete all preview payloads in the selected operator scope."""

    if scope == "legacy":
        if group_id:
            raise ValueError("le preview legacy non contengono group_id")
        prefixes = ["previews/"]
    elif group_id:
        prefixes = [tenant_domain_prefix(group_id, "previews")]
    else:
        # The tenant id sits before the domain, therefore a global canonical
        # purge must scan tenants/ and retain only parsed preview keys.
        prefixes = ["tenants/"]

    storage = get_runtime_object_storage()
    deleted = 0
    for prefix in prefixes:
        cursor = None
        while True:
            page = storage.list_objects_page(prefix, limit=500, cursor=cursor)
            for raw in page.get("objects") or []:
                key = str(raw.get("key") or "")
                parsed = parse_tenant_key(key)
                is_preview = (
                    parsed is not None and parsed.domain == "previews"
                ) or (scope == "legacy" and legacy_domain(key) == "previews")
                if not is_preview:
                    continue
                storage.delete(key)
                deleted += 1
            cursor = page.get("next_cursor")
            if not cursor:
                break
    return deleted


__all__ = [
    "StorageObjectEntry",
    "StorageObjectPage",
    "StorageProjectCatalog",
    "delete_preview_objects",
    "list_project_results",
    "list_runtime_objects",
]
