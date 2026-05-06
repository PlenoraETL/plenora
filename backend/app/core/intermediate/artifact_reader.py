"""
Intermediate full artifact reader — Batch API preview da artifact (2026-04-28).

Legge preview da `data.parquet` su runtime object storage per il modo
`full_artifact`. Read-only, no save/load nella chain — usato solo
dall'endpoint `GET /api/runs/{run_id}/nodes/{node_id}/data`.

Vincoli:
- Niente download su disco: stream bytes -> BytesIO -> pandas.read_parquet.
- Lettura bytes-first BOUNDED via
  ``app.core.resources.bounded_reader.read_stream_bounded`` con cap
  ``RESOURCE_MAX_BYTES`` (env, default 500 MB — mirror della
  Settings.DEFAULTS senza importare ``app.config``: stesso boundary
  pattern di ``result_blob.py``). Oversize NON viene degradato a
  errore best-effort: la ``MemoryLimitExceededError`` propaga al
  boundary FastAPI (handler ``app/main.py``) che mappa a HTTP 413
  con stage ``intermediate_artifact_preview``. Questo evita che un
  artifact corrotto/giga riempia RAM e OOM-killi il worker.
- Limite righe applicato lato pandas (`df.head(limit)`).
- Best-effort SOLO per errori non-OOM (capability off, key missing,
  decode fail): ritornano `ArtifactPreviewResult(rows=[],
  col_schema={}, error=...)`. L'endpoint puo' rispondere 200 con
  messaggio operativo invece di 500.
- Capability gate via `get_runtime_object_storage()` (cloud_backends).

Vedi anche `app/core/intermediate/artifact_writer.py` per il counterpart
write-side e per il layout key.

Anti-god-engine note:
``app.core.resources.bounded_reader`` is stdlib-only (no FastAPI/DB/
config); importing it from this leaf reader does not violate the
existing import boundary. The cap is read directly from
``os.environ['RESOURCE_MAX_BYTES']`` (mirror of
``Settings.DEFAULTS``) so the reader stays free of an
``app.config`` dependency, matching the result-blob loader's
pattern.
"""

from __future__ import annotations

import io
import logging
import os as _os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.resources.bounded_reader import read_stream_bounded
from app.shared.exceptions import MemoryLimitExceededError

logger = logging.getLogger(__name__)


_DEFAULT_ARTIFACT_READ_MAX_BYTES = 500 * 1024 * 1024  # 500 MB
_ARTIFACT_READ_MAX_BYTES_ENV = "RESOURCE_MAX_BYTES"


def _resolve_max_bytes() -> int:
    """Resolve the artifact-preview read cap.

    Mirror of ``app.core.resources.loaders.result_blob._resolve_max_bytes``:
    read ``RESOURCE_MAX_BYTES`` from ``os.environ`` at each call so
    monkeypatch / runtime env override takes effect without restart.
    Invalid / non-positive values fall back to 500 MB.
    """
    raw = _os.environ.get(_ARTIFACT_READ_MAX_BYTES_ENV)
    if not raw:
        return _DEFAULT_ARTIFACT_READ_MAX_BYTES
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_ARTIFACT_READ_MAX_BYTES
    return v if v > 0 else _DEFAULT_ARTIFACT_READ_MAX_BYTES


def _short_label(artifact_uri: str, max_len: int = 60) -> str:
    """Operator-visible short identifier for the bounded-read label.

    Avoids leaking full storage paths to the client via the boundary
    handler (which echoes ``path_basename`` in the HTTP-413 envelope).
    """
    if not artifact_uri:
        return "<none>"
    if len(artifact_uri) <= max_len:
        return artifact_uri
    return f"{artifact_uri[:12]}...{artifact_uri[-30:]}"


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

    Best-effort per errori di connettivita'/decode: il caller riceve
    un ``ArtifactPreviewResult`` con ``error`` valorizzato e nessuna
    eccezione viene sollevata.

    Eccezione tipata: ``MemoryLimitExceededError`` viene PROPAGATA al
    boundary FastAPI (HTTP 413) quando l'artifact supera
    ``RESOURCE_MAX_BYTES``. Questo e' intenzionale: trasformare
    oversize in error best-effort nasconderebbe il cap di risorsa al
    client e all'operator.
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
    # La materializzazione e' BOUNDED: l'iteratore viene abortito al
    # primo chunk che supererebbe ``RESOURCE_MAX_BYTES``. Oversize
    # NON viene mascherato da error best-effort: ``MemoryLimitExceededError``
    # propaga al boundary handler (HTTP 413 con stage
    # ``intermediate_artifact_preview``).
    max_bytes = _resolve_max_bytes()
    try:
        stream = storage.get_object_stream(artifact_uri)
    except Exception as e:
        logger.warning(
            "[ARTIFACT-READ] download artifact fallito per %s: %s",
            artifact_uri, e,
        )
        return ArtifactPreviewResult(
            error=f"download artifact fallito: {type(e).__name__}: {e}",
        )

    try:
        body_bytes = read_stream_bounded(
            stream["body"],
            max_bytes=max_bytes,
            label=_short_label(artifact_uri),
            stage="intermediate_artifact_preview",
        )
    except MemoryLimitExceededError as oversize:
        logger.error(
            "[ARTIFACT-READ] preview artifact ABORTED oversize "
            "artifact_uri=%s observed_bytes=%s limit_bytes=%s",
            _short_label(artifact_uri),
            oversize.observed_bytes,
            oversize.limit_bytes,
        )
        # Typed/controlled propagation: boundary handler maps to HTTP 413.
        # NOT degraded to best-effort `error=...`: silently returning
        # empty rows on a 50-GB artifact would hide the resource cap
        # signal from the operator and from the client.
        raise
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
