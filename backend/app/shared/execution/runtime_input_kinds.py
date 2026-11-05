"""Tabella UNICA dei tipi di input runtime (nodo «Input di progetto»).

Ogni tipo si dichiara qui, una volta, con:

- ``value``: come entra nella catena. ``file`` sostituisce una sorgente dati
  (binding ``data_source``); ``scalar`` scrive un parametro di un nodo
  (binding ``node_field``, stesse capability di
  ``app/core/eseguibili/binding_capabilities.py``); ``connection`` sceglie la
  connessione dell'intero progetto (nessun binding).
- ``modes``: i tipi di progetto in cui si puo' dichiarare.
- ``source``: chi fornisce il valore. ``client`` = l'utente lo scrive in
  «Prima di avviare»; ``server`` = lo inietta SOLO il server dall'utente
  autenticato (``app/shared/execution/runtime_identity``): un valore inviato
  dal browser per quell'input si rifiuta.
- ``source_modes``: i tipi di progetto in cui un input ``scalar`` puo' anche
  entrare COME SORGENTE (nodo con ``as_source``: una tabella di una riga e
  una colonna, ``source_column``). Per ``file`` = sempre sorgente.

Il ruolo di un nodo nel canvas (sorgente dati, parametro, connessione) si
legge solo da :func:`project_input_role`: validazione, inferenza degli
schemi, manifest ed esecuzione usano la stessa regola.

La copia del frontend (``frontend/src/utils/runtimeInputKinds.js``) si
confronta con il contratto ``backend/tests2/contracts/runtime_input_kinds.json``
generato da questa tabella (test di parita' da entrambi i lati).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

PROJECT_TYPES: tuple[str, ...] = ("estrai", "manipola", "arricchisci")

RuntimeInputValue = Literal["file", "scalar", "connection"]
RuntimeInputSource = Literal["client", "server"]
ProjectInputRole = Literal["source", "parameter", "connection"]

AS_SOURCE_FIELD = "as_source"
SOURCE_COLUMN_FIELD = "source_column"


@dataclass(frozen=True)
class RuntimeInputKindSpec:
    kind: str
    value: RuntimeInputValue
    modes: tuple[str, ...] = PROJECT_TYPES
    source: RuntimeInputSource = "client"
    source_modes: tuple[str, ...] = ()

    @property
    def server_injected(self) -> bool:
        return self.source == "server"

    def to_contract(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "value": self.value,
            "modes": list(self.modes),
            "source": self.source,
            "source_modes": list(self.source_modes),
        }


RUNTIME_INPUT_KINDS: tuple[RuntimeInputKindSpec, ...] = (
    RuntimeInputKindSpec("file", "file", source_modes=PROJECT_TYPES),
    RuntimeInputKindSpec("text", "scalar"),
    RuntimeInputKindSpec("integer", "scalar"),
    RuntimeInputKindSpec("number", "scalar"),
    RuntimeInputKindSpec("boolean", "scalar"),
    RuntimeInputKindSpec("date", "scalar"),
    RuntimeInputKindSpec("datetime", "scalar"),
    RuntimeInputKindSpec("select", "scalar"),
    RuntimeInputKindSpec("connection", "connection", modes=("estrai",)),
    # «Utente che esegue»: username di login dell'utente autenticato che
    # avvia l'esecuzione, iniettato dal server (mai dal client). In Manipola
    # puo' anche essere la sorgente (una riga, una colonna).
    RuntimeInputKindSpec("current_user", "scalar", source="server", source_modes=("manipola",)),
)

RUNTIME_INPUT_KIND_NAMES: tuple[str, ...] = tuple(spec.kind for spec in RUNTIME_INPUT_KINDS)
SPEC_BY_KIND: dict[str, RuntimeInputKindSpec] = {spec.kind: spec for spec in RUNTIME_INPUT_KINDS}
SERVER_INJECTED_KINDS: frozenset[str] = frozenset(
    spec.kind for spec in RUNTIME_INPUT_KINDS if spec.server_injected
)

# Literal derivato dalla tabella: lo usano il manifest e il modello del canvas.
RuntimeInputKind = Literal[RUNTIME_INPUT_KIND_NAMES]  # type: ignore[valid-type]


def kind_spec(kind: object) -> RuntimeInputKindSpec | None:
    """Spec del tipo (default ``file`` come il compilatore del manifest)."""
    return SPEC_BY_KIND.get(str(kind or "file").strip().lower())


def is_server_injected(kind: object) -> bool:
    spec = kind_spec(kind)
    return bool(spec and spec.server_injected)


def node_value(node: Any, key: str) -> Any:
    """Campo di un nodo del canvas: ``data`` prima, poi il nodo (dict o modello)."""
    if isinstance(node, Mapping):
        data = node.get("data")
        if isinstance(data, Mapping) and data.get(key) is not None:
            return data.get(key)
        return node.get(key)
    data = getattr(node, "data", None)
    if isinstance(data, Mapping) and data.get(key) is not None:
        return data.get(key)
    return getattr(node, key, None)


def node_type(node: Any) -> Any:
    """Tipo del nodo (sempre dal nodo, mai da ``data``)."""
    return node.get("type") if isinstance(node, Mapping) else getattr(node, "type", None)


def project_input_role(node: Any) -> ProjectInputRole | None:
    """Ruolo nel canvas di un nodo «Input di progetto» (None per gli altri nodi).

    ``source``: alimenta il flusso dati (file; scalare con ``as_source`` dove
    il tipo lo consente); ``parameter``: scrive parametri di altri nodi;
    ``connection``: sceglie la connessione del progetto.
    """
    if node_type(node) != "project_input":
        return None
    spec = kind_spec(node_value(node, "input_kind"))
    if spec is None:
        return "parameter"
    if spec.value == "file":
        return "source"
    if spec.value == "connection":
        return "connection"
    if spec.source_modes and bool(node_value(node, AS_SOURCE_FIELD)):
        return "source"
    return "parameter"


def contract_rows() -> list[dict[str, object]]:
    """Righe del contratto condiviso con il frontend."""
    return [spec.to_contract() for spec in RUNTIME_INPUT_KINDS]


__all__ = [
    "AS_SOURCE_FIELD",
    "PROJECT_TYPES",
    "RUNTIME_INPUT_KINDS",
    "RUNTIME_INPUT_KIND_NAMES",
    "RuntimeInputKind",
    "RuntimeInputKindSpec",
    "SERVER_INJECTED_KINDS",
    "SOURCE_COLUMN_FIELD",
    "SPEC_BY_KIND",
    "contract_rows",
    "is_server_injected",
    "kind_spec",
    "node_type",
    "node_value",
    "project_input_role",
]
