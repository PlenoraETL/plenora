"""cancel_task scrive un flag di revoca su PG (Plenora 2.0, Fase 5 F5.2).

Rimpiazza celery app.control.revoke: l'annullamento e' cooperativo (flag su
PostgreSQL), non un terminate broker-side.
"""

from __future__ import annotations

import pytest

from app.core.orchestrator.task_revocation_store import InMemoryTaskRevocationStore
from app.core.orchestrator.task_service import TaskService


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
]


class _FakeRuntime:
    app = object()

    def __init__(self) -> None:
        self.revoked: list[tuple] = []

    def revoke(self, task_id, *, terminate=False):
        self.revoked.append((task_id, terminate))


class _FakeMetadataStore:
    def put(self, metadata):
        pass

    def get(self, task_id):
        return None


def _service(revocations):
    return TaskService(
        runtime=_FakeRuntime(),
        metadata_store=_FakeMetadataStore(),
        revocation_store=revocations,
    )


def test_cancel_task_writes_revocation_flag():
    revocations = InMemoryTaskRevocationStore()
    service = _service(revocations)

    assert service.cancel_task("t1") is True
    assert revocations.is_revoked("t1") is True


def test_cancel_task_does_not_call_broker_revoke():
    revocations = InMemoryTaskRevocationStore()
    runtime = _FakeRuntime()
    service = TaskService(
        runtime=runtime,
        metadata_store=_FakeMetadataStore(),
        revocation_store=revocations,
    )

    service.cancel_task("t1")

    # L'annullamento e' cooperativo su PG: nessun terminate broker-side.
    assert runtime.revoked == []


def test_cancel_task_returns_false_on_store_error():
    class _BrokenStore:
        def revoke(self, task_id, *, ttl_seconds=None):
            raise RuntimeError("PG giu'")

        def is_revoked(self, task_id):
            return False

        def clear(self, task_id):
            pass

        def cleanup_expired(self):
            return 0

    service = _service(_BrokenStore())
    assert service.cancel_task("t1") is False
