"""Validatore PUT-time per i nodi api_enrich (arricchisci) — Epic Canvas Validation 1.6.

Porta la regola pure-PUT del FE: ARRICCHISCI_API_NOT_SELECTED (nessuna API connessa).

NON porta (per onestà, vedi probe 2026-06-01):
- ARRICCHISCI_API_CONNECTION_STALE → richiede la lista delle connessioni API
  disponibili (contesto esterno);
- ARRICCHISCI_PARAM_MAPPING_MISSING → richiede lo schema-parametri della
  connessione API (lookup DB) o uno snapshot a save-time.
Queste restano per quando l'engine avrà quel contesto (niente validatore-token).
"""

from __future__ import annotations

from typing import Any, List

from app.shared.execution.schema_types import InferredSchema
from app.shared.execution.validation_protocol import register_node_validator
from app.shared.execution.validation_types import Issue


class ArricchisciNodeValidator:
    """``validate_node`` per i nodi api_enrich."""

    def validate_node(self, node: Any, *, upstream: InferredSchema) -> List[Issue]:
        data = getattr(node, "data", None) or {}
        if not data.get("api_id"):
            label = data.get("label") or data.get("api_name") or getattr(node, "id", "")
            return [Issue(
                node_id=getattr(node, "id", ""), severity="error",
                code="ARRICCHISCI_API_NOT_SELECTED",
                message=f'Nodo "{label}": nessuna API connessa. Seleziona una connessione API.',
                suggestion=None,
            )]
        return []


def register_arricchisci_validator() -> None:
    """Registra il validatore api_enrich al composition-root (#D)."""
    register_node_validator("api_enrich", ArricchisciNodeValidator())


__all__ = ["ArricchisciNodeValidator", "register_arricchisci_validator"]
