"""Storage dispatch policy for persisted results."""

from __future__ import annotations

from app.shared.resources.storage_dispatch import resolve_payload_target


def storage_policy() -> str:
    """Return the normalized result storage policy."""
    from app.config import settings

    return (settings.get("STORAGE_POLICY", "hybrid") or "hybrid").strip().lower()


def result_s3_threshold() -> int:
    """Return the hybrid threshold from typed settings."""
    from app.config import settings

    return int(settings.get("RESULT_S3_THRESHOLD_BYTES"))


def result_db_hard_cap() -> int:
    """Return the largest payload allowed for DB-backed result blobs."""
    from app.config import settings

    return int(settings.get("RESULT_DB_HARD_CAP_BYTES", 500 * 1024 * 1024))


def should_use_object_storage(size: int, policy: str) -> bool:
    """Resolve whether a result payload should be written to object storage."""
    return (
        resolve_payload_target(
            policy=policy,
            size_bytes=size,
            threshold_bytes=result_s3_threshold(),
        )
        == "object_storage"
    )


__all__ = [
    "result_db_hard_cap",
    "result_s3_threshold",
    "should_use_object_storage",
    "storage_policy",
]
