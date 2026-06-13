"""
Pure no-op cleanroom beat tick smoke task.

Plenora 2.0 Fase 5: funzione pura (niente piu' @celery_app.task). L'actor
Dramatiq omonimo vive in workers/dramatiq_app.py.
"""


def cleanroom_beat_ping() -> str:
    """Return the canonical "beat_pong" string. Pure no-op."""
    return "beat_pong"
