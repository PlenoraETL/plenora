"""
Intermediate full artifact reader — Batch API preview da artifact (2026-04-28).

Legge preview da `data.parquet` su runtime object storage per il modo
`full_artifact`. Read-only, no save/load nella chain — usato solo
dall'endpoint `GET /api/runs/{run_id}/nodes/{node_id}/data`.

Vincoli:
- Niente download su disco: stream bytes bounded -> preview parser leaf.
- Lettura bytes-first BOUNDED via
  ``app.core.resources.bounded_reader.read_stream_bounded`` con cap
  DEDICATO ``ARTIFACT_PREVIEW_MAX_BYTES`` (env, default 25 MB).
  BE-GO-READY-RUNTIME-002-CENTRALIZE-PREVIEW-CAP-RESOLVER: il
  parsing dell'env vive nel canonico
  ``app.core.resources.preview_caps.resolve_artifact_preview_max_bytes``
  (gemello sibling di ``resource_caps.resolve_resource_max_bytes``,
  shape identica ma cap/env distinti). La preview ha un cap molto
  piu' basso del generico ``RESOURCE_MAX_BYTES`` (default 200 MB
  post BE-GO-READY-MEMORY-ENGINE-005) perche' la preview vive sul
  thread-pool dell'API ed e' concorrente per definizione: N
  tab/utenti che fanno refresh di N nodi non possono sommare
  200 MB ciascuno o l'API satura la RAM del processo prima di
  un OOM kill. Oversize NON viene degradato a errore
  best-effort: la ``MemoryLimitExceededError`` propaga al boundary
  FastAPI (handler ``app/main.py``) che mappa a HTTP 413 con stage
  ``intermediate_artifact_preview``. Questo evita che un artifact
  corrotto/giga riempia RAM e OOM-killi il worker.
- Limite righe applicato dal parser leaf via batch streaming
  (`preview_parser.parse_parquet_preview`, pyarrow iter_batches).
- Best-effort SOLO per errori non-OOM (capability off, key missing,
  decode fail): ritornano `ArtifactPreviewResult(rows=[],
  col_schema={}, error=...)`. L'endpoint puo' rispondere 200 con
  messaggio operativo invece di 500.
- Capability gate via `get_runtime_object_storage()` (cloud_backends).

Vedi anche `app/core/intermediate/artifact_writer.py` per il counterpart
write-side e per il layout key.

Anti-god-engine note:
``app.core.resources.bounded_reader`` and
``app.core.resources.preview_caps`` are stdlib-only (no
FastAPI/DB/config); importing them from this leaf reader does
not violate the existing import boundary. The cap is read via
``preview_caps.resolve_artifact_preview_max_bytes`` (canonical
parsing surface mirroring ``resource_caps.resolve_resource_max_bytes``),
so the reader stays free of an ``app.config`` dependency and of
duplicated env-parsing code.

BE-GO-READY-RESOURCE-ENGINE-016 (parser-boundary split): the
pandas-bound parsing of the downloaded byte buffer is delegated
to the leaf module ``app.core.intermediate.preview_parser``.
This file is now a pure I/O orchestrator -- it owns capability
gating, bounded streaming, and best-effort error envelope
construction; it does NOT import pandas or perform any
DataFrame work. ``import pandas`` lives ONLY inside
``preview_parser.parse_parquet_preview`` (lazy, inside the
function body). A source-bound test in
``tests2/intermediate/unit/test_artifact_reader_bounded_preview_contract.py``
locks that the reader does not regress on the pandas-import
edge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.intermediate.preview_parser import (
    ParquetPreviewParseError,
    parse_parquet_preview,
)
from app.core.resources.bounded_reader import read_stream_bounded
from app.core.resources.preview_caps import (
    resolve_artifact_preview_max_bytes,
)
from app.shared.exceptions import MemoryLimitExceededError

logger = logging.getLogger(__name__)


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
    ``ARTIFACT_PREVIEW_MAX_BYTES`` (default 25 MB, indipendente da
    ``RESOURCE_MAX_BYTES``). Questo e' intenzionale: trasformare
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
    # ma il boundary object-storage ci consegna chunks bytes; la
    # materializzazione del buffer e' BOUNDED e il decode resta nel
    # parser leaf (`preview_parser.parse_parquet_preview`, pyarrow
    # iter_batches), non in questo orchestrator.
    # L'iteratore viene abortito al
    # primo chunk che supererebbe ``ARTIFACT_PREVIEW_MAX_BYTES``
    # (default 25 MB — preview-specific cap, separato da
    # ``RESOURCE_MAX_BYTES``). Oversize NON viene mascherato da error
    # best-effort: ``MemoryLimitExceededError`` propaga al boundary
    # handler (HTTP 413 con stage ``intermediate_artifact_preview``).
    max_bytes = resolve_artifact_preview_max_bytes()
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

    # BE-GO-READY-RESOURCE-ENGINE-016: parsing the bounded byte
    # buffer is delegated to the leaf parser module. Pandas lives
    # ONLY inside ``preview_parser.parse_parquet_preview``;
    # ``artifact_reader`` (this file) is a pure I/O orchestrator
    # and contains zero pandas references at any indentation
    # level. The error envelope shape -- ``read_parquet
    # fallito: ...`` and ``to_dict fallito: ...`` strings --
    # is preserved verbatim so router callers and existing
    # contract tests do not regress.
    try:
        parsed = parse_parquet_preview(body_bytes, limit=limit)
    except ParquetPreviewParseError as e:
        if e.stage == "read_parquet":
            logger.warning(
                "[ARTIFACT-READ] read_parquet fallito per %s: %s",
                artifact_uri, e.cause,
            )
            return ArtifactPreviewResult(
                error=(
                    f"read_parquet fallito: "
                    f"{type(e.cause).__name__}: {e.cause}"
                ),
            )
        # stage == "to_dict": preserve the partial schema +
        # row_count so the caller learns the dataset shape even
        # when individual rows could not be materialized.
        return ArtifactPreviewResult(
            row_count=e.partial_row_count,
            col_schema=e.partial_schema,
            error=(
                f"to_dict fallito: "
                f"{type(e.cause).__name__}: {e.cause}"
            ),
        )

    return ArtifactPreviewResult(
        rows=parsed.rows,
        col_schema=parsed.col_schema,
        row_count=parsed.row_count,
        error=None,
    )
