"""Display helpers for configuration diagnostics.

Keep human-facing CLI output out of ``app.config`` so Settings remains a
configuration container plus compatibility shims.
"""

from __future__ import annotations

from typing import Any


def print_config(settings: Any) -> None:
    print("=" * 60)
    print("CONFIGURAZIONE PLENORA-ETL")
    print("=" * 60)
    print("\nDIRECTORY")
    print(f"  DATA_DIR:            {settings.DATA_DIR}")
    print(f"  LOGS_DIR:            {settings.LOGS_DIR}")
    print(f"  CONNECTIONS_DIR:     {settings.CONNECTIONS_DIR}")
    print(f"  RESULTS_DIR:         {settings.RESULTS_DIR}")
    print("\nTIMEOUT")
    print(f"  DB_QUERY_TIMEOUT:    {settings.DB_QUERY_TIMEOUT}s")
    print(f"  API_TIMEOUT:         {settings.API_TIMEOUT}s")
    print(f"  CELERY_TASK_TIMEOUT: {settings.CELERY_TASK_TIMEOUT}s")
    print("\nCELERY (Redis)")
    print(f"  BROKER_URL:          {settings.CELERY_BROKER_URL}")
    print(f"  RESULT_BACKEND:      {settings.CELERY_RESULT_BACKEND}")
    print("=" * 60)


__all__ = ["print_config"]
