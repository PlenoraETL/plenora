"""Test infrastructure helpers (test-only, NON importare da prod code).

Phase 1 Test Reliability Batch:
  - `celery_eager`: harness per invocare Celery task in-process eager
    (niente broker Redis reale)
  - `enrichment_task_fixtures`: builder canvas minimi per i contract test
    behavior-bound del task `execute_enrichment_task`
"""
