"""
Results DB Service - accesso a PostgreSQL (ex SQLite results.db).

Contiene: results (output pipeline). Execution-log ownership lives in
app.core.orchestrator.execution_logs.
"""

# KEEP_COMPAT_WITH_REMOVAL_CONDITION: remove after result blob and metadata
# tests import app.core.results.* boundaries directly.
# Owner: STORAGE_ARTIFACT_ENGINE

from typing import Optional

from app.core.results.cleanup import (
    delete_result,
    delete_results_for_project,
)
from app.core.results.db import (
    get_latest_result,
    get_result,
    list_results,
    save_result,
)
from app.db.pg_connection import close_connection, transaction


def init_db() -> None:
    """No-op per PostgreSQL: tabelle gia' create."""
    pass


# RESULTS
# KEEP_COMPAT: metadata names are re-exported from app.core.results.db.
# Blob reads remain here until their compatibility surface moves.
# KEEP_COMPAT: cleanup names are re-exported from app.core.results.cleanup.


def _s3_download_bytes(storage_key: str) -> Optional[bytes]:
    """Scarica un oggetto S3-backed come bytes. None su errore loggato.

    Thin back-compat wrapper su
    ``app.core.resources.loaders.result_blob._download_object_storage``.
    La firma e la semantica observable sono invariate.
    """
    from app.core.resources.loaders.result_blob import (
        _download_object_storage,
    )

    return _download_object_storage(storage_key)


def get_result_data(
    result_id: str, group_id: Optional[str] = None
) -> Optional[bytes]:
    """Compatibility wrapper for the canonical result blob boundary.

    Production callers should import
    ``app.core.results.blob.get_result_data`` directly. This shim remains for
    legacy/test imports and preserves the same optional tenant filter.
    """
    from app.core.results.blob import get_result_data as _get_result_data

    return _get_result_data(result_id, group_id=group_id)
