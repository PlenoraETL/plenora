"""
Pure no-op cleanroom beat tick smoke task.

"""

from workers.celery_app import celery_app


@celery_app.task(name="cleanroom.beat_ping", ignore_result=False, bind=False)
def cleanroom_beat_ping() -> str:
    """Return the canonical "beat_pong" string. Pure no-op."""
    return "beat_pong"
