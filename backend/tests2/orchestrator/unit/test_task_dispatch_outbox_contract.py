"""Transactional task dispatch outbox contracts."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from app.core.orchestrator.task_dispatch_outbox import PgTaskDispatchOutbox
from app.core.orchestrator.task_metadata_store import PendingTaskMetadata


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


class _Session:
    def __init__(self):
        self.executed = []
        self.rows = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchall(self, sql, params=()):
        self.executed.append((sql, params))
        return self.rows


def test_prepare_commits_metadata_and_dispatch_intent_in_one_uow(monkeypatch):
    from app.core.orchestrator import task_dispatch_outbox as module

    session = _Session()
    enters = []

    @contextmanager
    def fake_uow():
        enters.append(True)
        yield session

    monkeypatch.setattr(module, "unit_of_work", fake_uow)
    outbox = PgTaskDispatchOutbox(metadata_ttl_seconds=123)

    outbox.prepare(
        PendingTaskMetadata("task-1", "project-1", "execute"),
        actor_name="pipeline.execute",
        options={"kwargs": {"project_id": "project-1"}},
    )

    assert len(enters) == 1
    assert len(session.executed) == 2
    assert "INSERT INTO kv_store" in session.executed[0][0]
    assert "INSERT INTO task_dispatch_outbox" in session.executed[1][0]
    assert json.loads(session.executed[1][1][2])["kwargs"]["project_id"] == "project-1"


def test_claim_due_leases_rows_and_hydrates_options(monkeypatch):
    from app.core.orchestrator import task_dispatch_outbox as module

    session = _Session()
    session.rows = [
        {
            "task_id": "task-1",
            "actor_name": "pipeline.execute",
            "options": {"kwargs": {"project_id": "project-1"}},
            "attempts": 2,
        }
    ]

    @contextmanager
    def fake_uow():
        yield session

    monkeypatch.setattr(module, "unit_of_work", fake_uow)

    [item] = PgTaskDispatchOutbox(metadata_ttl_seconds=123).claim_due(limit=10)

    assert item.task_id == "task-1"
    assert item.options["kwargs"] == {"project_id": "project-1"}
    assert item.attempts == 2
    assert "DELETE FROM task_dispatch_outbox" in session.executed[0][0]
    assert "FOR UPDATE SKIP LOCKED" in session.executed[1][0]
