"""DramatiqTaskRuntime — adapter Dramatiq del protocol TaskRuntime.

Plenora 2.0, Fase 1: implementa ``send_task(name, **options)`` sopra
Dramatiq/RabbitMQ con dispatch by-name (Message costruito senza importare
l'actor: i moduli applicativi non dipendono dal package workers, come
oggi con Celery send_task).

Mappatura opzioni (superficie reale dei call-site censiti):
- ``args`` / ``kwargs`` → payload del messaggio;
- ``countdown`` (secondi, semantica Celery) → ``delay`` ms di Dramatiq;
- ``queue`` → coda di destinazione (default "default").

L'handle ritornato espone ``.id`` (message_id) come da
``TaskDispatchResult``. Import di dramatiq lazy: il modulo resta
importabile dove la dipendenza non e' installata (transizione).
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
        queue_name = options.pop("queue", None) or "default"
        # opzioni Celery senza equivalente diretto vengono ignorate in
        # modo esplicito qui (es. task_id custom non supportato): meglio
        # fallire la review che perdere semantica in silenzio.
        if options:
            raise ValueError(
                f"Opzioni send_task non mappate su Dramatiq: {sorted(options)}"
            )

        message = dramatiq.Message(
            queue_name=queue_name,
            actor_name=name,
            args=args,
            kwargs=kwargs,
            options={},
        )
        delay_ms = int(countdown * 1000) if countdown else None
        self._resolve_broker().enqueue(message, delay=delay_ms)
        return DramatiqDispatchHandle(id=message.message_id)


__all__ = ["DramatiqDispatchHandle", "DramatiqTaskRuntime"]
