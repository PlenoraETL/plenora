"""
Routes Package

Contiene tutti i router FastAPI dell'applicazione.
"""

from routes.tasks import router as tasks_router

__all__ = [
    "tasks_router",
]
