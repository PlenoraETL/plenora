"""DramatiqTaskRuntime: adapter Dramatiq del protocol TaskRuntime.

Implementa ``send_task(name, **options)`` sopra Dramatiq/RabbitMQ con
dispatch by-name. Il Message viene costruito senza importare l'actor, cosi'
i moduli applicativi non dipendono dal package workers.

Mappatura opzioni (superficie reale dei call-site censiti):
- ``args`` / ``kwargs`` → payload del messaggio;
- ``countdown`` (secondi, semantica Celery) → ``delay`` ms di Dramatiq;
- ``queue`` → coda di destinazione (default "default").
- ``task_id`` → ``message_id`` assegnato prima della pubblicazione.

L'handle ritornato espone ``.id`` (message_id) come da
``TaskDispatchResult``. Import di dramatiq lazy: il modulo resta
importabile dove la dipendenza non e' installata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class DramatiqDispatchHandle:
    """TaskDispatchResult minimale per il dispatch Dramatiq."""

    id: str


class DramatiqTaskRuntime:
    """TaskRuntime su broker Dramatiq (RabbitMQ in produzione)."""

    def __init__(self, broker: Optional[Any] = None) -> None:
        self._broker = broker

    def _resolve_broker(self) -> Any:
        if self._broker is not None:
            return self._broker
        import dramatiq

        return dramatiq.get_broker()

    def send_task(self, name: str, **options: Any) -> DramatiqDispatchHandle:
        import dramatiq

        args = tuple(options.pop("args", ()) or ())
        kwargs = dict(options.pop("kwargs", {}) or {})
        countdown = options.pop("countdown", None)
        queue_name = options.pop("queue", None)
        task_id = options.pop("task_id", None)
        # Le opzioni Celery senza equivalente diretto falliscono in modo
        # esplicito: meglio bloccare il dispatch che perderne la semantica.
        if options:
            raise ValueError(
                f"Opzioni send_task non mappate su Dramatiq: {sorted(options)}"
            )

        broker = self._resolve_broker()
        # Routing: senza queue esplicita, instrada sulla coda dell'actor
        # registrato (data-plane/control-plane/scheduler) — preserva la
        # topologia code di Celery. Fallback "default" se l'actor non e'
        # risolvibile (es. broker fake nei test, actor non registrato).
        if queue_name is None:
            queue_name = self._actor_queue_name(broker, name)

        message_kwargs = {
            "queue_name": queue_name,
            "actor_name": name,
            "args": args,
            "kwargs": kwargs,
            "options": {},
        }
        if task_id is not None:
            message_kwargs["message_id"] = str(task_id)
        message = dramatiq.Message(
            **message_kwargs,
        )
        delay_ms = int(countdown * 1000) if countdown else None
        broker.enqueue(message, delay=delay_ms)
        return DramatiqDispatchHandle(id=message.message_id)

    @staticmethod
    def _actor_queue_name(broker: Any, name: str) -> str:
        """Coda dell'actor registrato col nome dato, o 'default'."""
        try:
            return broker.get_actor(name).queue_name
        except Exception:
            return "default"


__all__ = ["DramatiqDispatchHandle", "DramatiqTaskRuntime"]
