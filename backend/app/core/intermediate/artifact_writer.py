"""
Intermediate full artifact writer — storage intermedi (2026-04-28).

Persiste lo snapshot completo di un nodo come `data.parquet` +
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.shared.storage_layout import INTERMEDIATE_PREFIX

logger = logging.getLogger(__name__)


# Manifest version. Bump quando cambia lo schema del manifest
MANIFEST_VERSION = 1


@dataclass(frozen=True)
class ArtifactPersistResult:
    """Esito di `persist_full_artifact`.

    `artifact_status` allinea i valori a `node_meta.artifact_status`:
      - "complete": data.parquet + manifest.json caricati
      - "failed": serialize/upload fallito; `error` popolato,
                    `artifact_uri=None` salvo manifest gia' uploadato
      - "none": non applicabile (caller non chiama questo modulo)
    """
    artifact_uri: Optional[str]
    artifact_format: str
    artifact_status: str
    error: Optional[str]


def _slug(value: str) -> str:
    """Sanitizza un identificatore canvas/group/project per l'uso nel
    path object storage. Il design assume che `run_id` sia un UUID e
    `group_id`/`project_id` siano gia' identificatori safe nel sistema,
    ma applichiamo un trim difensivo: niente slash, spazi mappati a `_`.

    NB: il safe set degli ID e' gia' enforced upstream
    (`app.db.config.projects.create_project`, ecc.). Qui non
    sostituiamo, per non rompere il roundtrip nel path. Solleviamo
    invece su valori palesemente invalidi (slash) per fail-fast.
    """
    if value is None:
        raise ValueError("identifier null nel layout artifact")
    s = str(value).strip()
    if not s:
        raise ValueError("identifier vuoto nel layout artifact")
    if "/" in s or "\\" in s or "\n" in s or "\r" in s:
        raise ValueError(
            f"identifier non valido nel layout artifact (slash/newline): {s!r}"
        )
    return s


def build_artifact_key_prefix(
    *,
    prefix: str,
    group_id: str,
    project_id: str,
    run_id: str,
    module: str,
    node_id: str,
    snapshot_kind: str,
) -> str:
    """Layout key deterministico (vedi modulo docstring).

    Ritorna il prefix CONDIVISO da `data.parquet` e `manifest.json`
    (entrambi vivono sotto `<prefix>/.../kind=.../`). Il caller
    appende il filename specifico.
    """
    p_clean = (prefix or INTERMEDIATE_PREFIX).strip().strip("/")
    return (
        f"{p_clean}/"
        f"group={_slug(group_id)}/"
        f"project={_slug(project_id)}/"
        f"run={_slug(run_id)}/"
        f"module={_slug(module)}/"
        f"node={_slug(node_id)}/"
        f"kind={_slug(snapshot_kind)}/"
    )


def _build_manifest(
    *,
    run_id: str,
    project_id: str,
    group_id: str,
    module: str,
    node_id: str,
    node_type: str,
    snapshot_kind: str,
    artifact_format: str,
    row_count: int,
    column_count: int,
    node_hash: Optional[str],
    hash_algo: Optional[str],
) -> dict:
    return {
        "artifact_version": MANIFEST_VERSION,
        "run_id": run_id,
        "project_id": project_id,
        "group_id": group_id,
        "module": module,
        "node_id": node_id,
        "node_type": node_type,
        "snapshot_kind": snapshot_kind,
        "format": artifact_format,
        "row_count": int(row_count),
        "column_count": int(column_count),
        "node_hash": node_hash,
        "hash_algo": hash_algo,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _serialize_dataframe_to_parquet(df: Any) -> bytes:
    """Serializza un DataFrame in bytes Parquet.

    `df.to_parquet(buf)` usa `pyarrow` se disponibile, altrimenti
    `fastparquet`. Niente `df.to_dict(full)` per definizione.

    Geometrie Shapely: `pyarrow` non le serializza nativamente. Il
    caller (snapshot.py) riceve l'eccezione e marca artifact_status
    = "failed" senza crashare la run. Soluzione strutturale (WKB
    pre-conversion / GeoParquet) e' fuori scope di questo batch.
    """
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", compression="snappy", index=False)
    return buf.getvalue()


def persist_full_artifact(
    *,
    df: Any,
    run_id: str,
    project_id: str,
    group_id: str,
    module: str,
    node_id: str,
    node_type: str,
    snapshot_kind: str,
    node_hash: Optional[str],
    hash_algo: Optional[str],
    row_count: int,
    column_count: int,
) -> ArtifactPersistResult:
    """Persiste data.parquet + manifest.json su runtime object storage.

    Best-effort: errori non sollevano. Ritorna sempre un
    `ArtifactPersistResult` che il caller persiste in `node_meta`.

    Order:
      1. Risolvi runtime ObjectStorage (capability gate).
      2. Compose key prefix deterministico.
      3. Serialize Parquet -> upload data.parquet.
      4. Serialize JSON manifest -> upload manifest.json.
      5. Ritorna `(uri=data.parquet, status="complete")`.

    Errori:
      - capability disabled / bucket missing: status="failed",
        error operativo.
      - serialize fail (geom, pyarrow missing): status="failed".
      - upload fail (rete, IAM): status="failed".
    """
    artifact_format = "parquet"

    # Lazy import: cloud_backends potrebbe non essere installato in
    # ambienti unit-test ridotti. La snapshot path resta safe: in caso
    # di import error registriamo "failed" e proseguiamo
    try:
        from app.shared.cloud_backends import (
            ObjectStorageDisabledError,
            get_runtime_object_storage,
        )
    except Exception as e:  # pragma: no cover - difensivo
        return ArtifactPersistResult(
            artifact_uri=None,
            artifact_format=artifact_format,
            artifact_status="failed",
            error=f"cloud_backends import fallito: {type(e).__name__}: {e}",
        )

    try:
        storage = get_runtime_object_storage()
    except ObjectStorageDisabledError as e:
        # Capability off: il caller dovrebbe averlo gia' bloccato in
        # validate_intermediate_settings. Se arriviamo qui
        # env mutato a runtime?), failed esplicito
        logger.warning(
            "[ARTIFACT] runtime object storage non disponibile per "
            "node=%s run=%s: %s",
            node_id, run_id, e,
        )
        return ArtifactPersistResult(
            artifact_uri=None,
            artifact_format=artifact_format,
            artifact_status="failed",
            error=str(e),
        )
    except Exception as e:
        logger.error(
            "[ARTIFACT] get_runtime_object_storage fallito per node=%s "
            "run=%s: %s",
            node_id, run_id, e,
            exc_info=True,
        )
        return ArtifactPersistResult(
            artifact_uri=None,
            artifact_format=artifact_format,
            artifact_status="failed",
            error=f"object storage non risolvibile: {e}",
        )

    # Layout key. `INTERMEDIATE_ARTIFACT_PREFIX` env override resta per
    # ops flexibility; il default e' la constant centralizzata. BE-CONFIG-001E
    # DEDUP): la chiave era letta sia via `Settings.DEFAULTS` (default
    # `"intermediate"`) sia direttamente da `os.getenv` qui. Migrato a
    # `settings.get(...)` per single source-of-default. La lettura tramite
    # Settings include eventuali override env via `_converti_tipo_env`
    # str pass-through). `or INTERMEDIATE_PREFIX` resta come safety net
    # se Settings ritornasse None per qualche motivo (e.g. test che
    # rimuove la default key); il default reale ora vive in Settings.DEFAULTS
    from app.config import settings as _settings
    prefix_env = _settings.get("INTERMEDIATE_ARTIFACT_PREFIX") or INTERMEDIATE_PREFIX
    try:
        key_prefix = build_artifact_key_prefix(
            prefix=prefix_env,
            group_id=group_id,
            project_id=project_id,
            run_id=run_id,
            module=module,
            node_id=node_id,
            snapshot_kind=snapshot_kind,
        )
    except ValueError as e:
        return ArtifactPersistResult(
            artifact_uri=None,
            artifact_format=artifact_format,
            artifact_status="failed",
            error=f"layout key invalido: {e}",
        )

    data_key = f"{key_prefix}data.parquet"
    manifest_key = f"{key_prefix}manifest.json"

    # 1. Parquet payload
    try:
        parquet_bytes = _serialize_dataframe_to_parquet(df)
    except Exception as e:
        logger.error(
            "[ARTIFACT] serialize Parquet fallito per node=%s run=%s: %s",
            node_id, run_id, e,
            exc_info=True,
        )
        return ArtifactPersistResult(
            artifact_uri=None,
            artifact_format=artifact_format,
            artifact_status="failed",
            error=f"serialize parquet fallito: {type(e).__name__}: {e}",
        )

    # 2. Upload data.parquet
    try:
        storage.upload_bytes(parquet_bytes, data_key)
    except Exception as e:
        logger.error(
            "[ARTIFACT] upload data.parquet fallito per node=%s run=%s: %s",
            node_id, run_id, e,
            exc_info=True,
        )
        return ArtifactPersistResult(
            artifact_uri=None,
            artifact_format=artifact_format,
            artifact_status="failed",
            error=f"upload data.parquet fallito: {type(e).__name__}: {e}",
        )

    # 3. Manifest
    manifest = _build_manifest(
        run_id=run_id,
        project_id=project_id,
        group_id=group_id,
        module=module,
        node_id=node_id,
        node_type=node_type,
        snapshot_kind=snapshot_kind,
        artifact_format=artifact_format,
        row_count=row_count,
        column_count=column_count,
        node_hash=node_hash,
        hash_algo=hash_algo,
    )
    try:
        manifest_bytes = json.dumps(
            manifest, indent=2, ensure_ascii=False,
        ).encode("utf-8")
        storage.upload_bytes(manifest_bytes, manifest_key)
    except Exception as e:
        # data.parquet gia' caricato: l'artifact e' parzialmente persistito
        # Marcare status="failed" per visibilita' operativa; l'orphan
        # Parquet e' meno grave del silent success
        logger.error(
            "[ARTIFACT] upload manifest.json fallito per node=%s run=%s "
            "(data.parquet gia' uploadato come %s): %s",
            node_id, run_id, data_key, e,
            exc_info=True,
        )
        return ArtifactPersistResult(
            artifact_uri=data_key,  # path del solo Parquet
            artifact_format=artifact_format,
            artifact_status="failed",
            error=f"upload manifest.json fallito: {type(e).__name__}: {e}",
        )

    return ArtifactPersistResult(
        artifact_uri=data_key,
        artifact_format=artifact_format,
        artifact_status="complete",
        error=None,
    )
