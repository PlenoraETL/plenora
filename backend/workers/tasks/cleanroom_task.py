"""
Pure no-op cleanroom smoke task.

Plenora 2.0 Fase 5: funzione pura (niente piu' @celery_app.task). L'actor
Dramatiq omonimo vive in workers/dramatiq_app.py.
"""


def cleanroom_ping() -> str:
    """Return the canonical "pong" string. Pure no-op."""
    return "pong"
