"""Registry + protocol per la validazione per-nodo (#D — composition-root).

I motori (estrai/manipola/arricchisci/carica) **registrano** qui il loro
``validate_node`` al composition-root (stesso pattern di
``register_multilayer_adapter``); l'orchestratore ``canvas_validation`` li scopre
via ``validator_for(node_type)`` senza importarli.

Direzione delle dipendenze: motori → ``shared``, mai ``shared`` → motori.

NB: la firma di ``validate_node`` è provvisoria (stabilizza in Fase 1.6, quando i
4 validatori vengono scritti). Per ora: il nodo + l'insieme dei nomi-colonna
disponibili a monte (coerente con ``check_mapping(columns: set[str], spec)``, #B).
"""

from __future__ import annotations

from typing import AbstractSet, Any, List, MutableMapping, Optional, Protocol, runtime_checkable

from app.shared.execution.validation_types import Issue


@runtime_checkable
class SupportsNodeValidation(Protocol):
    def validate_node(
        self, node: Any, *, available_columns: AbstractSet[str]
    ) -> List[Issue]:
        ...


_VALIDATORS: MutableMapping[str, SupportsNodeValidation] = {}


def register_node_validator(node_type: str, validator: SupportsNodeValidation) -> None:
    """Registra (o sostituisce) il validatore per un ``node_type``."""
    _VALIDATORS[node_type] = validator


def validator_for(node_type: str) -> Optional[SupportsNodeValidation]:
    """Il validatore registrato per ``node_type``, o ``None`` se assente."""
    return _VALIDATORS.get(node_type)


def registered_node_types() -> AbstractSet[str]:
    """Insieme dei ``node_type`` con un validatore registrato (per introspezione)."""
    return frozenset(_VALIDATORS)


def clear_node_validators() -> None:
    """Svuota il registry (isolamento test / re-bootstrap)."""
    _VALIDATORS.clear()


__all__ = [
    "SupportsNodeValidation",
    "register_node_validator",
    "validator_for",
    "registered_node_types",
    "clear_node_validators",
]
