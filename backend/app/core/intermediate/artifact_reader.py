"""
Intermediate full artifact reader — Batch API preview da artifact (2026-04-28).

Legge preview da `data.parquet` su runtime object storage per il modo
`full_artifact`. Read-only, no save/load nella chain — usato solo
dall'endpoint `GET /api/runs/{run_id}/nodes/{node_id}/data`.

Vincoli:
- Niente download su disco: stream bytes -> BytesIO -> pandas.read_parquet.
- Limite righe applicato lato pandas (`df.head(limit)`).
- Best-effort: errori (capability off, key missing, decode fail)
  ritornano `ArtifactPreviewResult(rows=[], col_schema={}, error=...)`.
  L'endpoint puo' rispondere 200 con messaggio operativo invece di 500.
- Capability gate via `get_runtime_object_storage()` (cloud_backends).

Vedi anche `app/core/intermediate/artifact_writer.py` per il counterpart
write-side e per il layout key.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ArtifactPreviewResult:
    """Esito di `load_preview_from_artifact`.

    `rows` e' gia' sanitizzato lato pandas (NaN/Inf rimangono come
    float Python; il caller in `router.py` applica `_sanitize_for_json`
    per rendere il payload JSON-strict).
    `col_schema` mappa `{col_name: dtype_str}`.
    `error` non-None segnala che la preview non e' stata caricata
    (capability off, key missing, parquet illeggibile, ecc.).
    """
    rows: List[Dict[str, Any]] = field(default_factory=list)
    col_schema: Dict[str, str] = field(default_factory=dict)
    row_count: int = 0
    error: Optional[str] = None


def load_preview_from_artifact(
    artifact_uri: str,
    *,
    limit: int = 100,
) -> ArtifactPreviewResult:
    """Scarica `data.parquet` via runtime object storage e ritorna le
    prime `limit` righe + schema.

    Best-effort: nessun errore solleva; il caller riceve un risultato
    con `error` valorizzato.
    """
    if not artifact_uri:
        return ArtifactPreviewResult(error="artifact_uri vuoto")
    if limit <= 0:
        return ArtifactPreviewResult(error=f"limit invalido: {limit}")

    try:
        from app.shared.cloud_backends import (
            ObjectStorageDisabledError,
            get_runtime_object_storage,
        )
    except Exception as e:  # pragma: no cover - difensivo
        return ArtifactPreviewResult(
            error=f"cloud_backends import fallito: {type(e).__name__}: {e}",
        )

    try:
        storage = get_runtime_object_storage()
    except ObjectStorageDisabledError as e:
        return ArtifactPreviewResult(
            error=f"object storage non disponibile: {e}",
        )
    except Exception as e:
        logger.error(
            "[ARTIFACT-READ] get_runtime_object_storage fallito per "
            "artifact_uri=%s: %s",
            artifact_uri, e,
            exc_info=True,
        )
        return ArtifactPreviewResult(
            error=f"object storage non risolvibile: {type(e).__name__}: {e}",
        )

    # Streaming download: la preview e' piccola (limit << dataset),
    # ma scaricare tutto il Parquet in BytesIO e' inevitabile per leggere
    # con `pd.read_parquet` (no random-access sui chunks via boto3 boundary).
    try:
        stream = storage.get_object_stream(artifact_uri)
        body_bytes = b"".join(stream["body"])
    except Exception as e:
        logger.warning(
            "[ARTIFACT-READ] download artifact fallito per %s: %s",
            artifact_uri, e,
        )
        return ArtifactPreviewResult(
            error=f"download artifact fallito: {type(e).__name__}: {e}",
        )

    try:
        import pandas as pd
        df = pd.read_parquet(io.BytesIO(body_bytes))
    except Exception as e:
        logger.warning(
            "[ARTIFACT-READ] read_parquet fallito per %s: %s",
            artifact_uri, e,
        )
        return ArtifactPreviewResult(
            error=f"read_parquet fallito: {type(e).__name__}: {e}",
        )

    row_count = int(len(df))
    head = df.head(int(limit))
    try:
        rows = head.to_dict(orient="records")
    except Exception as e:
        return ArtifactPreviewResult(
            row_count=row_count,
            col_schema={str(c): str(df[c].dtype) for c in df.columns},
            error=f"to_dict fallito: {type(e).__name__}: {e}",
        )

    col_schema = {str(c): str(df[c].dtype) for c in df.columns}
    return ArtifactPreviewResult(
        rows=rows,
        col_schema=col_schema,
        row_count=row_count,
        error=None,
    )
