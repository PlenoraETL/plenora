"""Copia dei dati per la dashboard Grafana di un Eseguibile (11/11/2026).

Chi decide: SOLO il server, all'avvio, scrivendo ``SUBJECT_FIELD`` nel
``execution_subject`` di tipo ``eseguibile`` del contratto (l'Eseguibile ha
una dashboard e Grafana e' abilitato). Il contesto degli executor la riceve
come ``CONTEXT_FIELD`` da ``PipelineExecutionContract.build_runtime_context``,
che toglie qualunque valore arrivato nel ``runtime_context`` (regola del
lettore: mai dal client). Gli executor la passano a
``ExecutableOutputPersistence(dashboard_copy=...)``, il punto unico che
scrive l'output e la copia.
"""

from __future__ import annotations

SUBJECT_FIELD = "dashboard"
CONTEXT_FIELD = "dashboard_copy"

__all__ = ["CONTEXT_FIELD", "SUBJECT_FIELD"]
