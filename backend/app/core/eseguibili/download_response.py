"""Consume-once HTTP response for executable output artifacts."""

from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException

from app.core.io.http_streaming import (
    CloseableStreamingResponse,
    CompletionTrackingIterator,
    safe_download_filename,
)
from app.utils.logger import get_logger

from . import download_claim_store, output_store


logger = get_logger(__name__)


def build_consume_once_response(
    *,
    task_id: str,
    group_id: str,
    storage_key: str,
    media_type: str,
    download_filename: object | None = None,
    fallback_filename: str = "output.bin",
) -> CloseableStreamingResponse:
    """Claim, stream and delete one executable output after complete EOF.

    A disconnected or failed transfer preserves the object for retry. The
    task-owner sidecar is retained after a successful transfer so the
    authenticated full-cleanup endpoint remains available until lifecycle
    retention removes the task-scoped metadata.
    """
    filename = safe_download_filename(
        download_filename if download_filename is not None else storage_key,
        fallback=fallback_filename,
    )
    claim_token = uuid4().hex
    try:
        claimed = download_claim_store.try_acquire(
            task_id=task_id,
            group_id=group_id,
            owner_token=claim_token,
        )
    except Exception as exc:
        logger.error(
            "Claim download eseguibile fallito task_id=%s: %s",
            task_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Download temporaneamente non disponibile",
        ) from exc
    if not claimed:
        raise HTTPException(status_code=409, detail="Output gia' in download")

    try:
        opened = output_store.open_stream(
            storage_key,
            expected_group_id=group_id,
        )
    except Exception as exc:
        try:
            download_claim_store.release(
                task_id=task_id,
                group_id=group_id,
                owner_token=claim_token,
            )
        except Exception:
            logger.warning(
                "Release claim dopo open_stream fallito task_id=%s",
                task_id,
                exc_info=True,
            )
        logger.error(
            "Apertura stream eseguibile fallita key=%s: %s",
            storage_key,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Errore apertura stream output",
        ) from exc

    size = int(opened.get("size") or 0)

    def _refresh_claim(_bytes_yielded: int) -> None:
        if not download_claim_store.refresh(
            task_id=task_id,
            group_id=group_id,
            owner_token=claim_token,
        ):
            raise RuntimeError("Lease download eseguibile persa")

    tracked_body = CompletionTrackingIterator(
        opened["body"],
        expected_size=size or None,
        on_progress=_refresh_claim,
    )

    def _cleanup_after_send() -> None:
        try:
            if tracked_body.completed:
                output_store.delete_task_outputs(
                    task_id,
                    group_id=group_id,
                    retain_owner=True,
                )
            else:
                logger.info(
                    "Download incompleto: output conservato task_id=%s bytes=%s",
                    task_id,
                    tracked_body.bytes_yielded,
                )
        except Exception as exc:
            logger.warning(
                "Cleanup storage post-download fallito task_id=%s: %s",
                task_id,
                exc,
            )
        finally:
            try:
                download_claim_store.release(
                    task_id=task_id,
                    group_id=group_id,
                    owner_token=claim_token,
                )
            except Exception:
                logger.warning(
                    "Release claim download fallito task_id=%s",
                    task_id,
                    exc_info=True,
                )

    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if size:
        headers["Content-Length"] = str(size)

    return CloseableStreamingResponse(
        tracked_body,
        media_type=media_type,
        headers=headers,
        on_close=_cleanup_after_send,
    )


__all__ = ["build_consume_once_response"]
