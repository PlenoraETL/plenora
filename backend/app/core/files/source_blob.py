"""Blob read for file sources (RAM-only).

Post-Phase4 refactor: il modulo non materializza piu' blob su disco.
`temp_file_for` e `temp_file_for_cm` sono stati rimossi — i consumatori
RAM-first usano `get_blob_from_db` direttamente e poi delegano a
`load_by_format_bytes` / `list_vector_layers` / `list_excel_sheets_from_bytes`.

Side-effect collaborators sono passati esplicitamente per testabilita'.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from app.core.resources.exceptions import MemoryLimitExceededError
from app.core.resources.file_source_blob_access import load_file_source_blob_bytes


AssertVisible = Callable[[str, str], None]
GetBlobFromDb = Callable[[str, Optional[str]], Optional[bytes]]


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
        return load_file_source_blob_bytes(
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


__all__ = [
    "AssertVisible",
    "GetBlobFromDb",
    "get_blob_from_db",
]
