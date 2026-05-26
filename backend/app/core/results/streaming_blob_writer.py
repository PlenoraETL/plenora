"""
Streaming writer per result blob: cursor query → S3 multipart o DB BYTEA.

Pensato per `QueryExecutor` quando il result e' streamable (CSV/Parquet
senza geometry): elimina l'output_path intermedio (`get_temp_output_path
+ save_result_blob(path)`) flushando direttamente i bytes prodotti dal
writer CSV/Parquet verso lo storage canonico.

Due mode-of-operation selezionati al construction time via
`storage_policy()`:

  - **object_storage / hybrid**: real S3 multipart upload via boto3
    low-level API (`create_multipart_upload` / `upload_part` /
    `complete_multipart_upload`). Buffer interno ~`PART_SIZE` (8MB
    default, allineato a `TransferConfig.multipart_chunksize` usato
    altrove). RAM footprint bounded.

  - **db**: accumulo in `io.BytesIO` enforced da `RESULT_DB_HARD_CAP_BYTES`.
    Cap check per ogni `.write()`: oltre il cap solleva
    `ResultSaveError` mid-stream invece di accumulare oltre il limite.

Interfaccia file-like minima (`write`, `tell`, `flush`) per i writer
CSV (`csv.writer` su `io.TextIOWrapper`) e PyArrow `ParquetWriter`
(accetta file-like writable). Cleanup S3 multipart su `abort()` ed
exception garantito.

USO TIPICO:
    writer = StreamingResultBlobWriter(
        project_id=..., filename="result.csv", fmt="csv",
        group_id=...,
    )
    try:
        # Write loop: csv.writer / pq.ParquetWriter.write_table(...)
        # Tutto attraverso `writer.write(bytes)`.
        writer.update_rows_count(total_rows)
        result_id = writer.finalize()
    except Exception:
        writer.abort()
        raise
"""

from __future__ import annotations

import io
import uuid
from typing import Optional

from app.core.results import store
from app.core.results.blob_writer import (
    ResultSaveError,
    apply_results_retention_after_save,
    close_failed_result_upload_intent,
    close_result_upload_intent,
    enforce_db_hard_cap,
    record_result_upload_intent,
    _runtime_storage_upload_error,
    _storage_key,
)
from app.core.results.storage_policy import (
    result_db_hard_cap,
    storage_policy,
)
from app.infrastructure.storage.compensation import compensating_delete
from app.utils.logger import get_logger

logger = get_logger(__name__)


# Part size minimo accettato da S3: 5 MB. Usiamo 8 MB per consistenza con
# `TransferConfig.multipart_chunksize` in `cloud_backends`. L'ultima part
# puo' essere < PART_SIZE senza errore.
PART_SIZE = 8 * 1024 * 1024


class StreamingResultBlobWriter:
    """File-like writer per result blob streaming.

    Non thread-safe: pensato per uso single-thread dal writer CSV/Parquet
    di `query_streaming`.

    Attributes:
        result_id: id stabile generato al `__init__` (disponibile prima
            del finalize per logging / event emission anticipata).
        rows_count: aggiornabile via `update_rows_count(n)` prima del
            finalize; viene scritto nella DB row.
    """

    def __init__(
        self,
        *,
        project_id: str,
        filename: str,
        fmt: str,
        rows_count: int = 0,
        group_id: Optional[str] = None,
    ) -> None:
        self.project_id = project_id
        self.filename = filename
        self.fmt = fmt
        self.rows_count = rows_count
        self.group_id = group_id
        self.result_id = str(uuid.uuid4())

        self._total_bytes = 0
        self._closed = False
        self._aborted = False

        policy = storage_policy()
        # Per streaming non conosciamo la size finale: scegliamo il
        # backend dalla policy stessa. `db` -> buffer; tutto il resto
        # (`object_storage`, `hybrid`) -> S3 multipart real-time.
        if policy == "db":
            self._mode = "db"
            self._init_db()
        else:
            self._mode = "s3"
            self._init_s3()
        logger.debug(
            f"StreamingResultBlobWriter init project_id={project_id} "
            f"result_id={self.result_id} mode={self._mode} fmt={fmt}"
        )

    # ── DB BYTEA mode ─────────────────────────────────────────────────

    def _init_db(self) -> None:
        self._db_buf = io.BytesIO()
        self._db_cap = result_db_hard_cap()

    def _write_db(self, data: bytes) -> int:
        if self._total_bytes + len(data) > self._db_cap:
            raise ResultSaveError(
                f"[StreamingResultBlobWriter/db] Streaming result excede "
                f"RESULT_DB_HARD_CAP_BYTES={self._db_cap} "
                f"(scritti {self._total_bytes} + chunk {len(data)}). "
                f"Cambia STORAGE_POLICY=object_storage o riduci la query."
            )
        return self._db_buf.write(data)

    def _finalize_db(self) -> str:
        data = self._db_buf.getvalue()
        # Difensivo: il cap e' enforced per-write, ricontrolla.
        enforce_db_hard_cap(len(data), ctx="StreamingResultBlobWriter/db")
        resolved_group_id = store.resolve_group_id_for_result(
            self.project_id, self.group_id,
        )
        store.save_result_row(
            result_id=self.result_id,
            project_id=self.project_id,
            group_id=resolved_group_id,
            filename=self.filename,
            fmt=self.fmt,
            data=data,
            rows_count=self.rows_count,
        )
        logger.info(
            f"Result {self.result_id} su DB (streaming, "
            f"{len(data)} bytes, rows={self.rows_count})"
        )
        return self.result_id

    # ── S3 multipart mode ─────────────────────────────────────────────

    def _init_s3(self) -> None:
        from app.infrastructure.storage.cloud_backends import (
            get_runtime_object_storage,
        )
        self._storage = get_runtime_object_storage()
        # Accesso al boto3 client + bucket via internal helpers del
        # backend S3. Usiamo `_client()` + `_extra_args()` + `bucket`
        # (parte dell'ABI interna del backend, stabile).
        self._client = self._storage._client()
        self._bucket = self._storage.bucket
        self._key = _storage_key(self.project_id, self.result_id)

        try:
            extra = self._storage._extra_args()
        except Exception:
            extra = {}

        resp = self._client.create_multipart_upload(
            Bucket=self._bucket, Key=self._key, **extra,
        )
        self._upload_id = resp["UploadId"]
        self._parts: list[dict] = []
        self._part_number = 0
        self._part_buf = bytearray()

        # Intent registrato pre-finalize: size_bytes=-1 perche' non noto
        # finche' lo stream non e' completo. La row finale aggiornera'
        # il size reale.
        self._intent_id = record_result_upload_intent(
            self._key,
            project_id=self.project_id,
            result_id=self.result_id,
            filename=self.filename,
            size_bytes=-1,
        )

    def _flush_full_parts(self) -> None:
        """Flush parts complete (>= PART_SIZE). Lascia il residuo in
        `_part_buf`. L'ultima part (< PART_SIZE) viene flush'a da
        `_finalize_s3` come final part."""
        while len(self._part_buf) >= PART_SIZE:
            chunk = bytes(self._part_buf[:PART_SIZE])
            del self._part_buf[:PART_SIZE]
            self._upload_one_part(chunk)

    def _upload_one_part(self, chunk: bytes) -> None:
        self._part_number += 1
        try:
            resp = self._client.upload_part(
                Bucket=self._bucket,
                Key=self._key,
                UploadId=self._upload_id,
                PartNumber=self._part_number,
                Body=chunk,
            )
        except Exception as exc:
            # Fail-fast: aborta lo stream qui per evitare di accumulare
            # parts orfane in S3.
            self._abort_s3_safe()
            raise _runtime_storage_upload_error(
                exc, result_id=self.result_id, filename=self.filename,
            ) from exc
        self._parts.append(
            {"PartNumber": self._part_number, "ETag": resp["ETag"]}
        )

    def _write_s3(self, data: bytes) -> int:
        self._part_buf.extend(data)
        self._flush_full_parts()
        return len(data)

    def _finalize_s3(self) -> str:
        # Flush della part residua (< PART_SIZE) come last part.
        if self._part_buf:
            self._upload_one_part(bytes(self._part_buf))
            self._part_buf = bytearray()

        if not self._parts:
            # Zero parts -> multipart complete non e' permesso da S3.
            # Aborta il multipart e usa put_object(Body=b"") per il caso
            # query vuota (es. 0 righe ritornate).
            try:
                self._client.abort_multipart_upload(
                    Bucket=self._bucket, Key=self._key, UploadId=self._upload_id,
                )
            except Exception as exc:
                logger.debug(f"abort_multipart_upload (zero parts): {exc}")
            try:
                self._client.put_object(
                    Bucket=self._bucket, Key=self._key, Body=b"",
                )
            except Exception as exc:
                close_failed_result_upload_intent(
                    self._intent_id, cleanup_outcome="ok",
                )
                raise _runtime_storage_upload_error(
                    exc, result_id=self.result_id, filename=self.filename,
                ) from exc
        else:
            try:
                self._client.complete_multipart_upload(
                    Bucket=self._bucket,
                    Key=self._key,
                    UploadId=self._upload_id,
                    MultipartUpload={"Parts": self._parts},
                )
            except Exception as exc:
                # Cleanup dei parts orfani (S3 conserva fino a lifecycle).
                self._abort_s3_safe()
                close_failed_result_upload_intent(
                    self._intent_id, cleanup_outcome="ok",
                )
                raise _runtime_storage_upload_error(
                    exc, result_id=self.result_id, filename=self.filename,
                ) from exc

        # Object su S3 ora canonical. Save DB row referente.
        resolved_group_id = store.resolve_group_id_for_result(
            self.project_id, self.group_id,
        )
        try:
            store.save_result_row(
                result_id=self.result_id,
                project_id=self.project_id,
                group_id=resolved_group_id,
                filename=self.filename,
                fmt=self.fmt,
                data=None,
                rows_count=self.rows_count,
                storage_key=self._key,
                size_bytes=self._total_bytes,
            )
            close_result_upload_intent(self._intent_id)
        except Exception:
            # DB write fallita post-upload: cleanup S3 + intent.
            cleanup_outcome = compensating_delete(
                self._key,
                domain="result",
                reason="db_save_failed_after_multipart_complete",
                storage=self._storage,
                extras={"project_id": self.project_id, "result_id": self.result_id},
                logger=logger,
            )
            close_failed_result_upload_intent(
                self._intent_id, cleanup_outcome=cleanup_outcome,
            )
            raise
        logger.info(
            f"Result {self.result_id} su object storage (streaming "
            f"multipart, {self._total_bytes // (1024 * 1024)} MB, "
            f"rows={self.rows_count}): {self._key}"
        )
        return self.result_id

    def _abort_s3_safe(self) -> None:
        """Best-effort abort del multipart upload. Idempotente: tollera
        upload gia' completato / abortito."""
        try:
            self._client.abort_multipart_upload(
                Bucket=self._bucket,
                Key=self._key,
                UploadId=self._upload_id,
            )
        except Exception as exc:
            logger.debug(
                f"abort_multipart_upload(result_id={self.result_id}): {exc}"
            )

    # ── File-like contract (write / tell / flush) ─────────────────────

    def write(self, data: bytes) -> int:
        if self._closed:
            raise RuntimeError(
                f"StreamingResultBlobWriter result_id={self.result_id} "
                f"is closed; cannot write more."
            )
        if not data:
            return 0
        n = self._write_db(data) if self._mode == "db" else self._write_s3(data)
        self._total_bytes += n
        return n

    def tell(self) -> int:
        return self._total_bytes

    def flush(self) -> None:
        # No-op: per S3 il flush avviene implicitamente in
        # `_flush_full_parts`. csv.writer / TextIOWrapper chiamano
        # `flush()` periodicamente; non vogliamo fare upload_part per
        # ogni flush sintetico.
        return None

    # ── Lifecycle ────────────────────────────────────────────────────

    def update_rows_count(self, n: int) -> None:
        """Aggiorna `rows_count` prima del finalize (la DB row la persiste)."""
        if self._closed:
            raise RuntimeError("StreamingResultBlobWriter is closed")
        self.rows_count = int(n)

    def finalize(self) -> str:
        """Completa lo stream: per S3 -> complete_multipart_upload +
        DB row. Per DB -> save_result_row con BYTEA. Ritorna `result_id`.

        Idempotente per re-call: solleva RuntimeError se gia' chiuso.
        """
        if self._closed:
            raise RuntimeError(
                f"StreamingResultBlobWriter result_id={self.result_id} "
                f"gia' finalizzato"
            )
        self._closed = True
        if self._mode == "db":
            rid = self._finalize_db()
        else:
            rid = self._finalize_s3()
        apply_results_retention_after_save(self.project_id)
        return rid

    def abort(self) -> None:
        """Aborta lo stream in modo idempotente. Su S3 cancella la
        multipart upload + intent. Su DB scarta il buffer."""
        if self._closed or self._aborted:
            return
        self._aborted = True
        self._closed = True
        if self._mode == "s3":
            self._abort_s3_safe()
            close_failed_result_upload_intent(
                self._intent_id, cleanup_outcome="ok",
            )
        else:
            self._db_buf = io.BytesIO()  # release memory
        logger.info(
            f"StreamingResultBlobWriter aborted result_id={self.result_id} "
            f"mode={self._mode} bytes_written={self._total_bytes}"
        )

    # Context manager: __exit__ aborta su exception, NON finalizza
    # automaticamente (il caller deve chiamare finalize esplicitamente
    # per ottenere il result_id).
    def __enter__(self) -> "StreamingResultBlobWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.abort()


__all__ = ["PART_SIZE", "StreamingResultBlobWriter"]
