"""Boundary di lettura per i blob FileSource.

Il data-plane canonico usa `open_blob_spool`: object storage resta chunked e
bounded fino a uno spool seekable, che effettua rollover su temp disk oltre la
soglia RAM e viene sempre chiuso. `get_blob_from_db` resta solo per i consumer
legacy che richiedono esplicitamente ``bytes``.

Side-effect collaborators sono passati esplicitamente per testabilita'.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, BinaryIO, Callable, Iterator, Optional

from app.core.resources.exceptions import MemoryLimitExceededError
from app.core.resources import file_source_blob_access
from app.core.io.bounded_streams import copy_chunks_to_spool
from app.core.resources.resource_caps import resolve_resource_max_bytes
from app.core.resources.types import Resource


AssertVisible = Callable[[str, str], None]
GetBlobFromDb = Callable[[str, Optional[str]], Optional[bytes]]


def _load_blob_stream_resource(
    file_id: str,
    *,
    expected_group_id: Optional[str],
    assert_visible: AssertVisible,
    superadmin_bypass: str,
) -> Resource:
    """Carica lo stream tenant-bound; i caller scelgono spool o HTTP stream."""
    assert_visible(file_id, expected_group_id or "")
    is_superadmin_caller = expected_group_id == superadmin_bypass
    resolved_group = (expected_group_id or "").strip() or superadmin_bypass

    def _fetch(is_superadmin: bool) -> Resource:
        return file_source_blob_access.load_file_source_blob_stream(
            file_source_id=file_id,
            expected_group_id=resolved_group,
            is_superadmin=is_superadmin,
        )

    try:
        resource = _fetch(is_superadmin_caller)
    except FileNotFoundError:
        if is_superadmin_caller:
            raise
        resource = _fetch(True)
    if resource.stream is None:
        raise FileNotFoundError(
            f"Nessuno stream disponibile per file_source {file_id!r}"
        )
    return resource


def get_blob_stream(
    file_id: str,
    *,
    expected_group_id: Optional[str] = None,
    assert_visible: AssertVisible,
    superadmin_bypass: str,
    logger: Any,
) -> Optional[Resource]:
    """Restituisce il Resource stream per consumer HTTP, fail-soft."""
    try:
        return _load_blob_stream_resource(
            file_id,
            expected_group_id=expected_group_id,
            assert_visible=assert_visible,
            superadmin_bypass=superadmin_bypass,
        )
    except PermissionError:
        # Tenant/visibility failures restano hard fail: mai degradarli a 404
        # generico dentro il boundary blob.
        raise
    except MemoryLimitExceededError as exc:
        logger.error(
            "[GESTITO] Stream FileSource %s oltre RESOURCE_MAX_BYTES: %s",
            file_id,
            exc,
        )
        return None
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.error(
            "[GESTITO] Errore apertura stream FileSource %s: %s",
            file_id,
            exc,
        )
        return None


def get_blob_from_db(
    file_id: str,
    *,
    expected_group_id: Optional[str] = None,
    assert_visible: AssertVisible,
    superadmin_bypass: str,
    logger: Any,
) -> Optional[bytes]:
    """Read the file_source blob, fail-soft.

    Tenant visibility remains fail-closed: callers inject the same
    FileSourceService._assert_visible callback used before separation.
    """
    # `_assert_visible` re-raises PermissionError to the caller. After it
    # returns, the caller has been authorized for this file_source.
    assert_visible(file_id, expected_group_id or "")

    is_superadmin_caller = expected_group_id == superadmin_bypass

    def _fetch(is_superadmin: bool) -> Optional[bytes]:
        return file_source_blob_access.load_file_source_blob_bytes(
            file_source_id=file_id,
            expected_group_id=(expected_group_id or "").strip()
            or superadmin_bypass,
            is_superadmin=is_superadmin,
        )

    try:
        return _fetch(is_superadmin=is_superadmin_caller)
    except FileNotFoundError:
        # Owned-path SQL filter excluded the row. Either the file genuinely
        # does not exist OR `_assert_visible` authorized via *_shares.
        if is_superadmin_caller:
            return None
        try:
            bypass_shared_fetch = not is_superadmin_caller
            return _fetch(is_superadmin=bypass_shared_fetch)
        except FileNotFoundError:
            return None
        except MemoryLimitExceededError as e:
            logger.error(
                f"[GESTITO] Blob {file_id} oversize su retry "
                f"con bypass loader: {e}. "
                f"Cap RESOURCE_MAX_BYTES limita la lettura."
            )
            return None
        except Exception as e:
            logger.error(
                f"[GESTITO] Errore lettura blob {file_id} (retry): {e}"
            )
            return None
    except MemoryLimitExceededError as e:
        logger.error(
            f"[GESTITO] Blob {file_id} oversize: {e}. "
            f"Cap RESOURCE_MAX_BYTES limita la lettura."
        )
        return None
    except Exception as e:
        logger.error(
            f"[GESTITO] Errore lettura blob {file_id}: {e}"
        )
        return None


@contextmanager
def open_blob_spool(
    file_id: str,
    *,
    expected_group_id: Optional[str] = None,
    assert_visible: AssertVisible,
    superadmin_bypass: str,
    logger: Any,
) -> Iterator[BinaryIO]:
    """Apre un FileSource come spool seekable bounded e auto-cleanup.

    S3 resta chunked fino allo spool; oltre ``IO_SPOOL_MAX_MEMORY_BYTES`` il
    payload grezzo passa a temp disk. Il DataFrame parser non riceve mai una
    copia ``bytes`` completa.
    """
    try:
        resource = _load_blob_stream_resource(
            file_id,
            expected_group_id=expected_group_id,
            assert_visible=assert_visible,
            superadmin_bypass=superadmin_bypass,
        )
        expected_size = resource.size_bytes if resource.size_bytes > 0 else None
        spool = copy_chunks_to_spool(
            resource.stream,
            max_bytes=resolve_resource_max_bytes(),
            expected_size=expected_size,
        )
        try:
            yield spool
        finally:
            spool.close()
    except MemoryLimitExceededError:
        logger.error(
            "[GESTITO] Stream FileSource %s oltre RESOURCE_MAX_BYTES",
            file_id,
        )
        raise


__all__ = [
    "AssertVisible",
    "GetBlobFromDb",
    "get_blob_from_db",
    "get_blob_stream",
    "open_blob_spool",
]
