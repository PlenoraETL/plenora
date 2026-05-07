"""
Pure no-op cleanroom smoke task.

"""

from workers.celery_app import celery_app


@celery_app.task(name="cleanroom.ping", ignore_result=False, bind=False)
def cleanroom_ping() -> str:
    """Return the canonical "pong" string. Pure no-op."""
    return "pong"
