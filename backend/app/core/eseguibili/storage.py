"""
Storage eseguibili su object storage cloud-agnostic.

Chiude R3 del multi-host audit: gli output degli eseguibili non devono
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, List
from urllib.parse import urlencode

from app.core.io.runtime_filesystem import upload_local_path
from app.shared.contracts.payloads import ObjectMap
from app.shared.resources.storage_layout import ESEGUIBILI_OUTPUTS_PREFIX
from app.core.io.tenant_namespace import (
    assert_tenant_key,
    build_tenant_key,
    tenant_domain_prefix,
)
from app.utils.logger import get_logger


logger = get_logger(__name__)


# Prefix object storage condiviso con il layout centrale; separato dal backup
# bucket pattern cosi' le lifecycle rule possono essere distinte.
PREFIX = ESEGUIBILI_OUTPUTS_PREFIX
EPHEMERAL_TAGS = {"plenora-retention": "ephemeral"}
INTERNAL_ARTIFACT_PREFIX = "__internal__"


def internal_artifact_filename(
    kind: str,
    source_id: str,
    filename: str,
) -> str:
    """Build a reserved basename for task-internal executable artifacts."""
    safe_kind = "".join(
        char for char in str(kind) if char.isalnum() or char in "-_"
    ) or "artifact"
    safe_source = "".join(
        char for char in str(source_id) if char.isalnum() or char in "-_"
    ) or "source"
    safe_name = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
    return (
        f"{INTERNAL_ARTIFACT_PREFIX}{safe_kind}__"
        f"{safe_source}__{safe_name}"
    )


def is_internal_artifact_key(key: str) -> bool:
    return key.rsplit("/", 1)[-1].startswith(INTERNAL_ARTIFACT_PREFIX)


def _storage():
    """Lazy load della factory cloud (evita hard dep su boto3 ai test unit).

    passa per `get_runtime_object_storage`. Solleva
    `ObjectStorageDisabledError` se la capability globale e' off; il
    caller (write/upload eseguibili output) deve trattarlo come
    "feature non disponibile" (eseguibili senza storage non possono
    persistere output → fail-fast con messaggio operativo)."""
    from app.core.io.object_storage import get_runtime_object_storage
    return get_runtime_object_storage()


def _key_for(group_id: str, task_id: str, filename: str) -> str:
    return build_tenant_key(group_id, PREFIX, task_id, filename)


def _mark_ephemeral(storage: Any, key: str) -> None:
    """Tag an output for bucket lifecycle; roll back if tagging fails."""
    try:
        storage.set_object_tags(key, EPHEMERAL_TAGS)
    except Exception:
        storage.delete(key)
        raise


def save_bytes(
    task_id: str, filename: str, data: bytes, *, group_id: str,
) -> str:
    """Upload diretto di bytes su object storage (no FS staging).

    Usato quando il produttore ha gia' i bytes in-memory (es.
    save_dataframe_to_bytes). Skippa completamente DATA_DIR/temp e
    anche /tmp del sistema: put_object(Body=bytes) boto3-style.

    Ritorna la key S3 usata.
    """
    key = _key_for(group_id, task_id, filename)
    storage = _storage()
    storage.upload_bytes(data, key)
    _mark_ephemeral(storage, key)
    logger.info(f"[storage] eseguibili output uploaded (in-memory): {key}")
    return key


# Part size minimo S3: 5 MB. 8 MB matchato a TransferConfig.multipart_chunksize
# di boto3 default (consistente con StreamingResultBlobWriter).
_PART_SIZE = 8 * 1024 * 1024


class ExecutableOutputWriter:
    """Forward-only multipart writer for one executable output.

    The query streaming path needs a binary file-like object, while the
    DataFrame exporters expose iterables of chunks.  This writer is the common
    boundary for both producers and deliberately does not create a ``results``
    row: executable ids are not project ids and therefore must never cross the
    ``results.project_id`` foreign-key boundary.
    """

    def __init__(self, task_id: str, filename: str, *, group_id: str) -> None:
        self.key = _key_for(group_id, task_id, filename)
        self._storage = _storage()
        self._client = self._storage._client()
        self._bucket = self._storage.bucket
        try:
            self._extra = self._storage._extra_args()
        except Exception:
            self._extra = {}
        self._extra = {
            **self._extra,
            "Tagging": urlencode(EPHEMERAL_TAGS),
        }
        response = self._client.create_multipart_upload(
            Bucket=self._bucket,
            Key=self.key,
            **self._extra,
        )
        self._upload_id = response["UploadId"]
        self._parts: list[dict] = []
        self._buffer = bytearray()
        self._position = 0
        self._closed = False

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._position

    @property
    def closed(self) -> bool:
        return self._closed

    def flush(self) -> None:
        # Multipart parts below the minimum S3 size cannot be flushed early.
        return None

    def write(self, data: bytes | bytearray | memoryview) -> int:
        if self._closed:
            raise ValueError("write su ExecutableOutputWriter chiuso")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("ExecutableOutputWriter accetta solo bytes")
        payload = bytes(data)
        self._buffer.extend(payload)
        self._position += len(payload)
        while len(self._buffer) >= _PART_SIZE:
            self._upload_part(bytes(self._buffer[:_PART_SIZE]))
            del self._buffer[:_PART_SIZE]
        return len(payload)

    def _upload_part(self, payload: bytes) -> None:
        part_number = len(self._parts) + 1
        try:
            response = self._client.upload_part(
                Bucket=self._bucket,
                Key=self.key,
                UploadId=self._upload_id,
                PartNumber=part_number,
                Body=payload,
            )
        except Exception as exc:
            raise RuntimeError(
                "ExecutableOutputWriter upload_part fallito "
                f"(key={self.key}): {exc}"
            ) from exc
        self._parts.append(
            {"PartNumber": part_number, "ETag": response["ETag"]}
        )

    def finalize(self) -> str:
        if self._closed:
            raise ValueError("ExecutableOutputWriter gia' chiuso")
        try:
            if self._buffer:
                self._upload_part(bytes(self._buffer))
                self._buffer.clear()
            if not self._parts:
                _abort_multipart_safe(
                    self._client,
                    self._bucket,
                    self.key,
                    self._upload_id,
                )
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=self.key,
                    Body=b"",
                    **self._extra,
                )
            else:
                self._client.complete_multipart_upload(
                    Bucket=self._bucket,
                    Key=self.key,
                    UploadId=self._upload_id,
                    MultipartUpload={"Parts": self._parts},
                )
            self._closed = True
            logger.info(
                "[storage] executable output uploaded streaming "
                f"(parts={len(self._parts)}): {self.key}"
            )
            return self.key
        except Exception:
            self.abort()
            raise

    def abort(self) -> None:
        if self._closed:
            return
        _abort_multipart_safe(
            self._client,
            self._bucket,
            self.key,
            self._upload_id,
        )
        self._buffer.clear()
        self._closed = True


def save_chunks(
    task_id: str,
    filename: str,
    chunks: Iterable[bytes],
    *,
    group_id: str,
) -> str:
    """Upload streaming via S3 multipart real-time.

    Mirror di `StreamingResultBlobWriter._init_s3` ma per il bucket
    eseguibili (no DB row coordination, no upload_intent). Consuma
    `chunks` (iterable di bytes) flushando part >= 8MB direttamente
    a `upload_part`. La part residua finale e' inviata da
    `complete_multipart_upload`.

    Pattern d'uso (enrichment_task is_eseguibile branch):
        save_chunks(
            task_id,
            filename,
            dataframe_to_export_chunks(df, fmt),
        )

    Peak RAM: ~`_PART_SIZE` (8MB) + chunk buffer del producer. Il
    DataFrame resta in RAM finche' il producer non lo serializza
    completamente; il payload finale NON viene mai materializzato
    intero.

    Args:
        task_id: id del task eseguibile (chiave nel prefix).
        filename: nome file destinazione (basename, no path).
        chunks: iterable di bytes. Iterable vuoto -> oggetto vuoto su S3.

    Returns:
        key S3 usata (`eseguibili-outputs/{task_id}/{filename}`).

    Raises:
        Errori boto3 si propagano al caller (logger.warning su abort).
        Su exception mid-stream chiama abort_multipart_upload per
        evitare parts orfane in S3.
    """
    writer = ExecutableOutputWriter(task_id, filename, group_id=group_id)
    try:
        for chunk in chunks:
            if not chunk:
                continue
            writer.write(chunk)
        return writer.finalize()
    except Exception:
        writer.abort()
        raise


def _abort_multipart_safe(
    client: Any,
    bucket: str,
    key: str,
    upload_id: str,
) -> None:
    """Cleanup helper: aborta multipart upload swallow-error (best-effort)."""
    try:
        client.abort_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id,
        )
    except Exception as exc:
        logger.debug(
            f"abort_multipart_upload (key={key}) failed: {exc}"
        )


def save(task_id: str, src_path: Path, *, group_id: str) -> str:
    """Upload del file su object storage, poi delete del local temp.

    Il provider usa upload_file/multipart direttamente dal path: nessuna copia
    completa del file viene creata nel processo worker.

    Ritorna la key usata (utile per audit/logging).
    """
    if not src_path.exists():
        raise FileNotFoundError(f"Source non trovato: {src_path}")
    if not src_path.is_file():
        raise ValueError(f"Source non e' un file: {src_path}")

    key = _key_for(group_id, task_id, src_path.name)
    storage = _storage()
    upload_local_path(storage, src_path, key)
    _mark_ephemeral(storage, key)
    logger.info(f"[storage] eseguibili output uploaded (path streaming): {key}")

    # Cleanup eager del local file dopo upload riuscito. Se l'upload
    # fallisce sopra, il file resta su disco per retry manuale.
    from app.core.io.local_cleanup import rmtree_safe
    rmtree_safe(src_path, logger=logger)
    # Cleanup parent dir se vuota (evita accumulo di dir orfane
    # tipo DATA_DIR/temp/urby_<pid>_<hash>/).
    try:
        parent = src_path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as e:
        logger.warning(f"Cleanup parent dir fallito per {src_path.parent}: {e}")

    return key


def find_keys(task_id: str, *, group_id: str) -> List[str]:
    """Lista le key oggetti appartenenti a `task_id`. Oggi tipicamente 1."""
    storage = _storage()
    prefix = f"{tenant_domain_prefix(group_id, PREFIX)}{task_id}/"
    # Legacy prefix remains readable until the migration has completed.
    objs = storage.list_objects(prefix) + storage.list_objects(
        f"{PREFIX}/{task_id}/"
    )
    return [o["key"] for o in objs]


def open_stream(key: str, *, expected_group_id: str) -> ObjectMap:
    """Apre uno stream di download. Delega al backend cloud."""
    assert_tenant_key(
        key,
        expected_group_id=expected_group_id,
        expected_domain=PREFIX,
        allow_legacy=True,
    )
    return _storage().get_object_stream(key)


def open_task_stream(
    task_id: str,
    key: str,
    *,
    expected_group_id: str,
) -> ObjectMap:
    """Open an executable artifact only if it belongs to the expected task."""
    safe_task_id = "".join(
        char for char in str(task_id) if char.isalnum() or char in "-_"
    )
    if not safe_task_id or safe_task_id != task_id:
        raise ValueError("task_id non valido")
    parsed = assert_tenant_key(
        key,
        expected_group_id=expected_group_id,
        expected_domain=PREFIX,
        allow_legacy=True,
    )
    if parsed is not None:
        belongs_to_task = (
            bool(parsed.relative_segments)
            and parsed.relative_segments[0] == safe_task_id
        )
    else:
        parts = str(key).split("/")
        belongs_to_task = len(parts) >= 3 and parts[1] == safe_task_id
    if not belongs_to_task:
        raise ValueError("artifact di un altro task")
    return _storage().get_object_stream(key)


def delete_key(key: str, *, expected_group_id: str) -> None:
    """Cancella una singola key. Idempotenza delegata al backend cloud."""
    assert_tenant_key(
        key,
        expected_group_id=expected_group_id,
        expected_domain=PREFIX,
        allow_legacy=True,
    )
    _storage().delete(key)


def delete_by_task(task_id: str, *, group_id: str) -> int:
    """Cancella tutti gli oggetti sotto `eseguibili-outputs/{task_id}/`.

    Ritorna count degli oggetti eliminati. Idempotente: se non c'e'
    niente, ritorna 0 senza errori.
    """
    storage = _storage()
    keys = find_keys(task_id, group_id=group_id)
    deleted = 0
    failures: list[tuple[str, Exception]] = []
    for key in keys:
        try:
            assert_tenant_key(
                key,
                expected_group_id=group_id,
                expected_domain=PREFIX,
                allow_legacy=True,
            )
            storage.delete(key)
            deleted += 1
        except Exception as e:
            failures.append((key, e))
            logger.warning(f"[storage] delete {key} fallito: {e}")
    if failures:
        failed_keys = ", ".join(key for key, _exc in failures)
        raise RuntimeError(
            f"Cleanup object storage incompleto: {len(failures)}/{len(keys)} "
            f"delete falliti ({failed_keys})"
        )
    return deleted
