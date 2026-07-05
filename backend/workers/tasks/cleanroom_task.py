"""
Pure no-op cleanroom smoke task.

Funzione pura invocata dall'actor Dramatiq omonimo in workers/dramatiq_app.py.
"""


def cleanroom_ping() -> str:
    """Return the canonical "pong" string. Pure no-op."""
    return "pong"
