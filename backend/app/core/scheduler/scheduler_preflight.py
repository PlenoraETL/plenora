"""Preflight checks for scheduler execution."""

from app.core.scheduler.models import ScheduleConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)


def pre_check_destination_connection(config: ScheduleConfig, connection_service) -> bool:
    """
    Verify the configured destination connection before recalculating a pipeline.
    """
    if not config.destination:
        return True

    connection_id = config.destination.connection_id

    try:
        connection = connection_service.get_connection(connection_id)
        if not connection:
            logger.error(f"[GESTITO] Connessione {connection_id} non trovata")
            return False

        result = connection_service.test_connection(connection_id)
        if result and result.success:
            logger.info(f"Pre-check OK: {connection_id}")
            return True

        logger.error(f"[GESTITO] Pre-check fallito: {connection_id}")
        return False

    except Exception as e:
        logger.error(f"[CRASH] Errore pre-check: {e}", exc_info=True)
        return False
