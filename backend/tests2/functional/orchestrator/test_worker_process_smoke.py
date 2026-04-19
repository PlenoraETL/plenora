"""Smoke minimo del percorso async-lite: il worker parte e resta vivo."""

from __future__ import annotations


def test_celery_worker_process_starts(celery_worker):
    proc = celery_worker["process"].process
    assert proc.poll() is None
