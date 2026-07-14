"""Migrate referenced runtime objects to ``tenants/{group_id}/...``.

Dry-run is the default. ``--apply`` performs server-side copies and updates
PostgreSQL pointers atomically per reference. Source objects are retained by
default. To remove them, validate the dry-run first and execute the initial
``--apply`` together with ``--delete-source``; a later run has no legacy DB
references from which to rediscover already migrated source keys.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from typing import Callable

from app.core.io.object_storage import get_runtime_object_storage
from app.core.io.tenant_namespace import build_tenant_key, legacy_domain
from app.db.pg_connection import unit_of_work
from app.shared.resources.storage_layout import (
    FILE_SOURCES_PREFIX,
    INTERMEDIATE_PREFIX,
    PREVIEWS_PREFIX,
    RESULTS_PREFIX,
)


@dataclass(frozen=True, slots=True)
class Candidate:
    domain: str
    owner_id: str
    group_id: str
    old_key: str
    new_key: str
    update: Callable[[], bool]


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with unit_of_work() as session:
        return session.fetchall(sql, params)


def _updated(sql: str, params: tuple) -> bool:
    with unit_of_work() as session:
        cursor = session.execute(sql, params)
        return bool(getattr(cursor, "rowcount", 0))


def _result_candidates() -> list[Candidate]:
    candidates: list[Candidate] = []
    for row in _rows(
        "SELECT id, project_id, group_id, storage_key FROM results "
        "WHERE storage_key IS NOT NULL AND storage_key NOT LIKE 'tenants/%'"
    ):
        old = str(row["storage_key"])
        if legacy_domain(old) != RESULTS_PREFIX:
            continue
        new = build_tenant_key(
            str(row["group_id"]), RESULTS_PREFIX,
            str(row["project_id"]), old.rsplit("/", 1)[-1],
        )
        result_id = str(row["id"])
        candidates.append(Candidate(
            RESULTS_PREFIX, result_id, str(row["group_id"]), old, new,
            lambda result_id=result_id, old=old, new=new: _updated(
                "UPDATE results SET storage_key=%s WHERE id=%s AND storage_key=%s",
                (new, result_id, old),
            ),
        ))
    return candidates


def _file_source_candidates() -> list[Candidate]:
    candidates: list[Candidate] = []
    for row in _rows(
        "SELECT id, group_id, storage_key FROM file_sources "
        "WHERE storage_key IS NOT NULL AND storage_key NOT LIKE 'tenants/%'"
    ):
        old = str(row["storage_key"])
        if legacy_domain(old) != FILE_SOURCES_PREFIX:
            continue
        relative = old.split("/")[1:]
        new = build_tenant_key(str(row["group_id"]), FILE_SOURCES_PREFIX, *relative)
        file_id = str(row["id"])
        candidates.append(Candidate(
            FILE_SOURCES_PREFIX, file_id, str(row["group_id"]), old, new,
            lambda file_id=file_id, old=old, new=new: _updated(
                "UPDATE file_sources SET storage_key=%s WHERE id=%s AND storage_key=%s",
                (new, file_id, old),
            ),
        ))
    return candidates


def _preview_candidates() -> list[Candidate]:
    candidates: list[Candidate] = []
    rows = _rows(
        "SELECT tr.task_id, tr.payload, p.group_id "
        "FROM task_results tr "
        "LEFT JOIN projects p ON p.id=tr.payload->>'project_id' "
        "WHERE jsonb_exists(tr.payload, 'preview_storage_key') "
        "AND tr.payload->>'preview_storage_key' NOT LIKE 'tenants/%'"
    )
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        old = str(payload.get("preview_storage_key") or "")
        group_id = str(row.get("group_id") or "")
        if legacy_domain(old) != PREVIEWS_PREFIX or not group_id:
            print(f"skip preview {row['task_id']}: tenant non determinabile")
            continue
        relative = old.split("/")[1:]
        new = build_tenant_key(group_id, PREVIEWS_PREFIX, *relative)
        task_id = str(row["task_id"])
        candidates.append(Candidate(
            PREVIEWS_PREFIX, task_id, group_id, old, new,
            lambda task_id=task_id, old=old, new=new, group_id=group_id: _updated(
                "UPDATE task_results SET payload=jsonb_set("
                "jsonb_set(payload, '{preview_storage_key}', to_jsonb(%s::text)), "
                "'{preview_group_id}', to_jsonb(%s::text)), updated_at=now() "
                "WHERE task_id=%s AND payload->>'preview_storage_key'=%s",
                (new, group_id, task_id, old),
            ),
        ))
    return candidates


def _intermediate_candidates() -> list[Candidate]:
    candidates: list[Candidate] = []
    for row in _rows(
        "SELECT nm.run_id, nm.node_id, nm.artifact_uri, r.group_id "
        "FROM node_meta nm JOIN runs r ON r.run_id=nm.run_id "
        "WHERE nm.artifact_uri IS NOT NULL "
        "AND nm.artifact_uri NOT LIKE 'tenants/%'"
    ):
        old = str(row["artifact_uri"])
        if legacy_domain(old) != INTERMEDIATE_PREFIX:
            continue
        relative = old.split("/")[1:]
        group_id = str(row["group_id"])
        new = build_tenant_key(group_id, INTERMEDIATE_PREFIX, *relative)
        run_id, node_id = str(row["run_id"]), str(row["node_id"])
        candidates.append(Candidate(
            INTERMEDIATE_PREFIX, f"{run_id}/{node_id}", group_id, old, new,
            lambda run_id=run_id, node_id=node_id, old=old, new=new: _updated(
                "UPDATE node_meta SET artifact_uri=%s "
                "WHERE run_id=%s AND node_id=%s AND artifact_uri=%s",
                (new, run_id, node_id, old),
            ),
        ))
    return candidates


def collect_candidates() -> list[Candidate]:
    return (
        _result_candidates()
        + _file_source_candidates()
        + _preview_candidates()
        + _intermediate_candidates()
    )


def _copy_verified(storage, old_key: str, new_key: str) -> None:
    client = storage._client()
    source = client.head_object(Bucket=storage.bucket, Key=old_key)
    extra = storage._extra_args()
    client.copy_object(
        Bucket=storage.bucket,
        Key=new_key,
        CopySource={"Bucket": storage.bucket, "Key": old_key},
        **extra,
    )
    destination = client.head_object(Bucket=storage.bucket, Key=new_key)
    if int(destination.get("ContentLength") or -1) != int(
        source.get("ContentLength") or -2
    ):
        client.delete_object(Bucket=storage.bucket, Key=new_key)
        raise RuntimeError(f"size mismatch dopo copy {old_key} -> {new_key}")


def run(*, apply: bool, delete_source: bool) -> int:
    candidates = collect_candidates()
    print(f"references da migrare: {len(candidates)}")
    for item in candidates:
        print(
            f"{item.domain} {item.owner_id}: {item.old_key} -> {item.new_key}"
        )
    if not apply:
        return 0

    storage = get_runtime_object_storage()
    copied: set[tuple[str, str]] = set()
    migrated = 0
    sources_to_delete: set[str] = set()
    for item in candidates:
        pair = (item.old_key, item.new_key)
        if pair not in copied:
            _copy_verified(storage, item.old_key, item.new_key)
            copied.add(pair)
        if item.update():
            migrated += 1
            sources_to_delete.add(item.old_key)
        else:
            storage.delete(item.new_key)
            raise RuntimeError(
                f"reference cambiata durante la migrazione: {item.owner_id}"
            )

    if delete_source:
        for key in sorted(sources_to_delete):
            storage.delete(key)
        print(f"source legacy eliminate: {len(sources_to_delete)}")
    else:
        print(
            "source legacy conservate; la loro rimozione richiede una nuova "
            "migrazione tracciata o cleanup manuale verificato"
        )
    print(f"references migrate: {migrated}")
    return migrated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--delete-source", action="store_true")
    args = parser.parse_args()
    if args.delete_source and not args.apply:
        parser.error("--delete-source richiede --apply")
    run(apply=args.apply, delete_source=args.delete_source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
