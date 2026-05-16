"""Celery task boundary for administrative database backups."""

from __future__ import annotations

from workers.celery_app import celery_app
from app.config import settings
from app.core.admin_backups.backup_run_store import release_manual_backup_lock
from app.utils.logger import get_logger

logger = get_logger(__name__)


@celery_app.task(
    name="workers.tasks.admin_backup_task.run_manual_backup_task",
    bind=True,
    max_retries=0,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=settings.CELERY_TASK_TIMEOUT - 60,
    time_limit=settings.CELERY_TASK_TIMEOUT,
)
def run_manual_backup_task(self, *, owner_token: str) -> dict[str, object]:
    """Run the existing backup job inside a worker, not the API process."""

    exit_code = 0
    try:
        from scripts import backup_db

        backup_db.main()
        return {
            "success": True,
            "status": "completed",
            "task_id": self.request.id,
        }
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
        if exit_code == 0:
            return {
                "success": True,
                "status": "completed",
                "task_id": self.request.id,
            }
        logger.error("Backup manuale fallito con exit_code=%s", exit_code)
        raise RuntimeError(f"manual_backup_failed:{exit_code}") from exc
    finally:
        try:
            release_manual_backup_lock(owner_token=owner_token)
        except Exception:
            logger.warning("release lock backup manuale fallita", exc_info=True)
