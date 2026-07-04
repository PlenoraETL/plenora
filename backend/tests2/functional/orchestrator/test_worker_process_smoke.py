"""Smoke minimo del percorso async-lite: il worker parte e resta vivo."""

from __future__ import annotations


def test_task_worker_process_starts(task_worker):
    proc = task_worker["process"].process
    assert proc.poll() is None
