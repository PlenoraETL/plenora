"""
Contratto DramatiqTaskRuntime (Plenora 2.0, Fase 1).

L'adapter deve soddisfare il protocol TaskRuntime e mappare la
superficie reale dei call-site (kwargs, countdown, args, queue) sul
messaggio Dramatiq, con dispatch by-name (nessun import degli actor).
"""

from __future__ import annotations

import pytest

dramatiq = pytest.importorskip("dramatiq", reason="dramatiq non installato (Fase 1)")

from app.core.task_runtime.dispatch import TaskRuntime  # noqa: E402
from app.core.task_runtime.dramatiq_runtime import (  # noqa: E402
    DramatiqDispatchHandle,
    DramatiqTaskRuntime,
)

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


class _FakeBroker:
    def __init__(self) -> None:
        self.enqueued: list[tuple] = []

    def enqueue(self, message, delay=None):
        self.enqueued.append((message, delay))
        return message


def test_satisfies_task_runtime_protocol():
    assert isinstance(DramatiqTaskRuntime(broker=_FakeBroker()), TaskRuntime)


def test_send_task_dispatches_by_name_with_kwargs():
    broker = _FakeBroker()
    runtime = DramatiqTaskRuntime(broker=broker)

    handle = runtime.send_task(
        "workers.tasks.admin_backup_task.run_manual_backup_task",
        kwargs={"owner_token": "tok-1"},
    )

    assert isinstance(handle, DramatiqDispatchHandle)
    [(message, delay)] = broker.enqueued
    assert message.actor_name == (
        "workers.tasks.admin_backup_task.run_manual_backup_task"
    )
    assert message.kwargs == {"owner_token": "tok-1"}
    assert message.args == ()
    assert message.queue_name == "default"
    assert delay is None
    assert handle.id == message.message_id


def test_countdown_maps_to_delay_milliseconds():
    broker = _FakeBroker()
    runtime = DramatiqTaskRuntime(broker=broker)

    runtime.send_task("schedule_pipeline_task", kwargs={"schedule_id": "s1"}, countdown=90)

    [(_, delay)] = broker.enqueued
    assert delay == 90_000


def test_preassigned_task_id_becomes_message_id():
    broker = _FakeBroker()

    handle = DramatiqTaskRuntime(broker=broker).send_task(
        "x", kwargs={}, task_id="custom-id"
    )

    [(message, _)] = broker.enqueued
    assert message.message_id == "custom-id"
    assert handle.id == "custom-id"


def test_unmapped_options_fail_loud():
    runtime = DramatiqTaskRuntime(broker=_FakeBroker())
    with pytest.raises(ValueError, match="non mappate"):
        runtime.send_task("x", kwargs={}, unsupported_option=True)


def test_queue_option_routes_message():
    broker = _FakeBroker()
    DramatiqTaskRuntime(broker=broker).send_task("x", queue="data_plane")
    [(message, _)] = broker.enqueued
    assert message.queue_name == "data_plane"


def test_send_task_routes_to_registered_actor_queue():
    # Senza queue esplicita, instrada sulla coda dell'actor registrato
    # (preserva la topologia: carica.execute -> data-plane).
    from types import SimpleNamespace

    class _ActorBroker(_FakeBroker):
        def get_actor(self, name):
            return SimpleNamespace(queue_name="data-plane")

    broker = _ActorBroker()
    DramatiqTaskRuntime(broker=broker).send_task("carica.execute", kwargs={})
    [(message, _)] = broker.enqueued
    assert message.queue_name == "data-plane"


def test_send_task_falls_back_to_default_when_actor_unknown():
    # Broker senza get_actor (fake) o actor non registrato -> default.
    broker = _FakeBroker()
    DramatiqTaskRuntime(broker=broker).send_task("ignoto", kwargs={})
    [(message, _)] = broker.enqueued
    assert message.queue_name == "default"
