"""
Storage eseguibili su object storage cloud-agnostic.

Chiude R3 del multi-host audit: gli output degli eseguibili non devono
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from app.shared.contracts.payloads import ObjectMap
from app.shared.resources.storage_layout import ESEGUIBILI_OUTPUTS_PREFIX
from app.utils.logger import get_logger


logger = get_logger(__name__)


# Prefix object storage condiviso con il layout centrale; separato dal backup
# bucket pattern cosi' le lifecycle rule possono essere distinte.
PREFIX = ESEGUIBILI_OUTPUTS_PREFIX


def _storage():
    """Lazy load della factory cloud (evita hard dep su boto3 ai test unit).

    passa per `get_runtime_object_storage`. Solleva
    `ObjectStorageDisabledError` se la capability globale e' off; il
    caller (write/upload eseguibili output) deve trattarlo come
    "feature non disponibile" (eseguibili senza storage non possono
    persistere output → fail-fast con messaggio operativo)."""
    from app.core.io.object_storage import get_runtime_object_storage
    return get_runtime_object_storage()


def _key_for(task_id: str, filename: str) -> str:
    return f"{PREFIX}/{task_id}/{filename}"


def save_bytes(task_id: str, filename: str, data: bytes) -> str:
    """Upload diretto di bytes su object storage (no FS staging).

    Usato quando il produttore ha gia' i bytes in-memory (es.
    save_dataframe_to_bytes). Skippa completamente DATA_DIR/temp e
    anche /tmp del sistema: put_object(Body=bytes) boto3-style.

    Ritorna la key S3 usata.
    """
    key = _key_for(task_id, filename)
    storage = _storage()
    storage.upload_bytes(data, key)
    logger.info(f"[storage] eseguibili output uploaded (in-memory): {key}")
    return key


# Part size minimo S3: 5 MB. 8 MB matchato a TransferConfig.multipart_chunksize
# di boto3 default (consistente con StreamingResultBlobWriter).
_PART_SIZE = 8 * 1024 * 1024


def save_chunks(task_id: str, filename: str, chunks) -> str:
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
    storage = _storage()
    key = _key_for(task_id, filename)

    # boto3 client + bucket via internal helper (ABI interna stabile
    # del backend S3, stesso pattern di StreamingResultBlobWriter).
    client = storage._client()
    bucket = storage.bucket
    try:
        extra = storage._extra_args()
    except Exception:
        extra = {}

    resp = client.create_multipart_upload(Bucket=bucket, Key=key, **extra)
    upload_id = resp["UploadId"]
    parts: list[dict] = []
    part_number = 0
    part_buf = bytearray()

    # Cleanup multipart centralizzato: ogni errore passa da un solo abort,
    # evitando doppie call S3 e log secondari che coprirebbero il failure
    # originale.
    # Ora le sub-call alzano direttamente: l'unico abort vive nel
    # `except` esterno.
    def _flush_one_part(payload: bytes) -> None:
        nonlocal part_number
        part_number += 1
        try:
            r = client.upload_part(
                Bucket=bucket, Key=key, UploadId=upload_id,
                PartNumber=part_number, Body=payload,
            )
        except Exception as exc:
            raise RuntimeError(
                f"eseguibili.save_chunks upload_part fallito (key={key}): {exc}"
            ) from exc
        parts.append({"PartNumber": part_number, "ETag": r["ETag"]})

    try:
        for chunk in chunks:
            if not chunk:
                continue
            part_buf.extend(chunk)
            while len(part_buf) >= _PART_SIZE:
                _flush_one_part(bytes(part_buf[:_PART_SIZE]))
                del part_buf[:_PART_SIZE]

        # Flusha la part residua finale (< _PART_SIZE).
        if part_buf:
            _flush_one_part(bytes(part_buf))
            part_buf = bytearray()

        if not parts:
            # Zero parts: S3 multipart non permette complete con 0 parts.
            # Aborta + put_object con corpo vuoto.
            _abort_multipart_safe(client, bucket, key, upload_id)
            client.put_object(Bucket=bucket, Key=key, Body=b"", **extra)
        else:
            try:
                client.complete_multipart_upload(
                    Bucket=bucket, Key=key, UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
            except Exception as exc:
                raise RuntimeError(
                    f"eseguibili.save_chunks complete fallito (key={key}): {exc}"
                ) from exc

        logger.info(
            f"[storage] eseguibili output uploaded streaming "
            f"(parts={len(parts)}): {key}"
        )
        return key
    except Exception:
        _abort_multipart_safe(client, bucket, key, upload_id)
        raise


def _abort_multipart_safe(client, bucket: str, key: str, upload_id: str) -> None:
    """Cleanup helper: aborta multipart upload swallow-error (best-effort)."""
    try:
        client.abort_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id,
        )
    except Exception as exc:
        logger.debug(
            f"abort_multipart_upload (key={key}) failed: {exc}"
        )


def save(task_id: str, src_path: Path) -> str:
    """Upload del file su object storage, poi delete del local temp.

    Thin wrapper su `save_bytes`: legge il contenuto del file una volta in
    RAM e delega al path bytes-first. Trade-off: streaming upload sostituito
    da buffered. Per gli eseguibili
    Plenora il payload e' di solito small-to-medium (REST file
    workflow output, < RESULT_DB_HARD_CAP); il tradeoff RAM e' accettabile.

    Ritorna la key usata (utile per audit/logging).
    """
    if not src_path.exists():
        raise FileNotFoundError(f"Source non trovato: {src_path}")
    if not src_path.is_file():
        raise ValueError(f"Source non e' un file: {src_path}")

    data = src_path.read_bytes()
    key = save_bytes(task_id, src_path.name, data)

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


def find_keys(task_id: str) -> List[str]:
    """Lista le key oggetti appartenenti a `task_id`. Oggi tipicamente 1."""
    storage = _storage()
    prefix = f"{PREFIX}/{task_id}/"
    objs = storage.list_objects(prefix)
    return [o["key"] for o in objs]


def open_stream(key: str) -> ObjectMap:
    """Apre uno stream di download. Delega al backend cloud."""
    return _storage().get_object_stream(key)


def delete_key(key: str) -> None:
    """Cancella una singola key. Idempotenza delegata al backend cloud."""
    _storage().delete(key)


def delete_by_task(task_id: str) -> int:
    """Cancella tutti gli oggetti sotto `eseguibili-outputs/{task_id}/`.

    Ritorna count degli oggetti eliminati. Idempotente: se non c'e'
    niente, ritorna 0 senza errori.
    """
    storage = _storage()
    keys = find_keys(task_id)
    for key in keys:
        try:
            storage.delete(key)
        except Exception as e:
            logger.warning(f"[storage] delete {key} fallito (non-fatal): {e}")
    return len(keys)
