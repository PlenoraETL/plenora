"""Canonical result blob read boundary.

Result metadata SQL lives in :mod:`app.core.results.db`; delete/cleanup lives
in :mod:`app.core.results.cleanup`. Blob reads delegate directly to the
Resource Engine dispatch.
"""

from __future__ import annotations

from typing import Optional


def get_result_data(result_id: str, group_id: Optional[str] = None) -> Optional[bytes]:
    """Return result blob bytes, preserving the legacy read contract.

    ``None`` means no visible row or unavailable object-storage payload. When
    ``group_id`` is provided, the Resource Engine applies the tenant SQL filter;
    omitting it preserves existing internal caller behavior.
    """
    from app.core.resources.loaders.result_blob import _dispatch_by_id

    data, _backend = _dispatch_by_id(
        result_id,
        group_id_filter=group_id or None,
    )
    return data


__all__ = ["get_result_data"]
