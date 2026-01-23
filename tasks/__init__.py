"""
Celery Tasks Package

Contiene tutti i task asincroni dell'applicazione.
"""

from tasks.enrichment_task import execute_enrichment_task

__all__ = [
    "execute_enrichment_task",
]
