"""Move legacy PostgreSQL payloads to the configured S3-compatible store.

Dry-run is the default. Use ``--apply`` after deploying the object-storage
configuration and the 2026-07-13 NOT VALID guard constraints.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.core.intermediate.artifact_writer import persist_full_artifact
from app.core.io.object_storage import get_runtime_object_storage
from app.core.orchestrator.preview_payload_store import persist_preview_payload
from app.db.pg_connection import unit_of_work
from app.shared.resources.storage_layout import FILE_SOURCES_PREFIX, RESULTS_PREFIX


def _bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise TypeError(f"payload DB non binario: {type(value).__name__}")


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with unit_of_work() as session:
        return session.fetchall(sql, params)


def _one(sql: str, params: tuple = ()) -> dict | None:
    with unit_of_work() as session:
        return session.fetchone(sql, params)


def _migrate_results(*, apply: bool) -> int:
    candidates = _rows(
        "SELECT id, project_id, filename, octet_length(data) AS size_bytes "
        "FROM results WHERE data IS NOT NULL ORDER BY created_at, id"
    )
    if not apply:
        print(f"results: {len(candidates)} legacy BYTEA")
        return len(candidates)
    storage = get_runtime_object_storage()
    moved = 0
    for meta in candidates:
        result_id = str(meta["id"])
        row = _one("SELECT data FROM results WHERE id=%s AND data IS NOT NULL", (result_id,))
        if not row:
            continue
        data = _bytes(row["data"])
        key = f"{RESULTS_PREFIX}/{meta['project_id']}/{result_id}.bin"
        storage.upload_bytes(data, key)
        with unit_of_work() as session:
            cur = session.execute(
                "UPDATE results SET storage_key=%s, size_bytes=%s, data=NULL "
                "WHERE id=%s AND data IS NOT NULL",
                (key, len(data), result_id),
            )
        if getattr(cur, "rowcount", 0) == 0:
            storage.delete(key)
            continue
        moved += 1
        print(f"result {result_id}: {len(data)} bytes -> {key}")
    return moved


def _migrate_file_sources(*, apply: bool) -> int:
    candidates = _rows(
        "SELECT id, original_filename, octet_length(data) AS size_bytes "
        "FROM file_sources WHERE data IS NOT NULL ORDER BY created_at, id"
    )
    if not apply:
        print(f"file_sources: {len(candidates)} legacy BYTEA")
        return len(candidates)
    storage = get_runtime_object_storage()
    moved = 0
    for meta in candidates:
        file_id = str(meta["id"])
        row = _one(
            "SELECT data FROM file_sources WHERE id=%s AND data IS NOT NULL",
            (file_id,),
        )
        if not row:
            continue
        data = _bytes(row["data"])
        filename = Path(str(meta.get("original_filename") or "file.bin")).name
        key = f"{FILE_SOURCES_PREFIX}/{file_id}/{filename}"
        storage.upload_bytes(data, key)
        with unit_of_work() as session:
            cur = session.execute(
                "UPDATE file_sources SET storage_key=%s, size_bytes=%s, data=NULL "
                "WHERE id=%s AND data IS NOT NULL",
                (key, len(data), file_id),
            )
        if getattr(cur, "rowcount", 0) == 0:
            storage.delete(key)
            continue
        moved += 1
        print(f"file_source {file_id}: {len(data)} bytes -> {key}")
    return moved


def _migrate_previews(*, apply: bool) -> int:
    candidates = _rows(
        "SELECT task_id, payload FROM task_results "
        "WHERE name='pipeline.preview' AND jsonb_exists(payload, 'rows') "
        "ORDER BY created_at"
    )
    if not apply:
        print(f"task preview: {len(candidates)} payload JSONB")
        return len(candidates)
    moved = 0
    for row in candidates:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        project_id = str(payload.get("project_id") or "")
        task_id = str(row["task_id"])
        # Preview prodotte dalle immagini precedenti al task envelope non
        # avevano project_id. Il prefisso legacy rende comunque la key S3
        # valida; rimuoviamo poi project_id dall'envelope per conservare la
        # precedente semantica ACL (visibile solo ad admin/superadmin quando
        # il task non e' associabile a un progetto).
        had_project_id = bool(project_id)
        storage_project_id = project_id or "legacy"
        envelope = persist_preview_payload(
            payload, project_id=storage_project_id, task_id=task_id
        )
        if not had_project_id:
            envelope.pop("project_id", None)
        with unit_of_work() as session:
            cur = session.execute(
                "UPDATE task_results SET payload=%s::jsonb, updated_at=now() "
                "WHERE task_id=%s AND jsonb_exists(payload, 'rows')",
                (json.dumps(envelope, separators=(",", ":")), task_id),
            )
        if getattr(cur, "rowcount", 0) > 0:
            moved += 1
        else:
            get_runtime_object_storage().delete(
                str(envelope["preview_storage_key"])
            )
    return moved


def _migrate_node_rows(*, apply: bool) -> int:
    candidates = _rows(
        "SELECT nm.run_id, nm.node_id, nm.node_type, nm.node_hash, nm.hash_algo, "
        "r.canvas_id AS project_id, r.group_id, count(nr.id) AS row_count "
        "FROM node_rows nr JOIN node_meta nm ON nm.run_id=nr.run_id AND nm.node_id=nr.node_id "
        "JOIN runs r ON r.run_id=nm.run_id "
        "GROUP BY nm.run_id,nm.node_id,nm.node_type,nm.node_hash,nm.hash_algo,r.canvas_id,r.group_id "
        "ORDER BY nm.run_id,nm.node_id"
    )
    if not apply:
        print(f"node_rows: {len(candidates)} snapshot legacy")
        return len(candidates)
    import pandas as pd

    moved = 0
    for meta in candidates:
        rows = _rows(
            "SELECT row_data FROM node_rows WHERE run_id=%s AND node_id=%s ORDER BY row_idx",
            (meta["run_id"], meta["node_id"]),
        )
        normalized_rows = []
        for row in rows:
            value = row["row_data"]
            if isinstance(value, str):
                value = json.loads(value)
            if not isinstance(value, dict):
                raise TypeError(
                    f"node_rows.row_data non e' un oggetto JSON: {type(value).__name__}"
                )
            normalized_rows.append(value)
        frame = pd.DataFrame(normalized_rows)
        artifact = persist_full_artifact(
            df=frame,
            run_id=str(meta["run_id"]),
            project_id=str(meta["project_id"]),
            group_id=str(meta["group_id"]),
            module="legacy",
            node_id=str(meta["node_id"]),
            node_type=str(meta.get("node_type") or "unknown"),
            snapshot_kind="final",
            node_hash=meta.get("node_hash"),
            hash_algo=meta.get("hash_algo"),
            row_count=len(frame),
            column_count=len(frame.columns),
        )
        if artifact.artifact_status != "complete" or not artifact.artifact_uri:
            print(f"node {meta['run_id']}/{meta['node_id']}: FAILED {artifact.error}")
            continue
        with unit_of_work() as session:
            session.execute(
                "UPDATE node_meta SET persistence_mode='full_artifact', "
                "persisted_rows_count=0, artifact_uri=%s, artifact_format=%s, "
                "artifact_status='complete', artifact_error=NULL "
                "WHERE run_id=%s AND node_id=%s",
                (artifact.artifact_uri, artifact.artifact_format, meta["run_id"], meta["node_id"]),
            )
            session.execute(
                "DELETE FROM node_rows WHERE run_id=%s AND node_id=%s",
                (meta["run_id"], meta["node_id"]),
            )
        moved += 1
    return moved


def _validate_guard_constraints() -> None:
    """Validate the NOT VALID guards after every legacy payload is gone."""
    checks = (
        ("results", "results_payload_external_only", "data IS NOT NULL"),
        ("file_sources", "file_sources_payload_external_only", "data IS NOT NULL"),
        ("node_rows", "node_rows_legacy_read_only", "TRUE"),
        (
            "task_results",
            "task_results_no_tabular_payload",
            "jsonb_exists(payload, 'rows')",
        ),
    )
    for table, constraint, where in checks:
        row = _one(f"SELECT count(*) AS n FROM {table} WHERE {where}")
        remaining = int((row or {}).get("n") or 0)
        if remaining:
            print(
                f"constraint {constraint}: NON validato, payload residui={remaining}"
            )
            continue
        with unit_of_work() as session:
            session.execute(
                f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint}"
            )
        print(f"constraint {constraint}: VALIDATA")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="esegue la migrazione")
    args = parser.parse_args()
    print("mode:", "APPLY" if args.apply else "DRY-RUN")
    counts = {
        "results": _migrate_results(apply=args.apply),
        "file_sources": _migrate_file_sources(apply=args.apply),
        "previews": _migrate_previews(apply=args.apply),
        "intermediate_snapshots": _migrate_node_rows(apply=args.apply),
    }
    if args.apply:
        _validate_guard_constraints()
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
