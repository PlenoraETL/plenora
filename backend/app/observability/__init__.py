"""Observability boundary — health/readiness, telemetry helpers.

BE-GO-READY-BOOTSTRAP-ENGINE-009-HEALTH-EXTRACTION: marker package.
The first member is `health.py`, extracted from `app/main.py`. New
observability surfaces (e.g. object storage health, Celery worker
health, prometheus metrics) should land here as additive modules.
"""
