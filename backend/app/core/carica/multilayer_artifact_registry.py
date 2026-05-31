"""Registry generico: artefatto upstream -> N layer per il sink ArcGIS multi-layer.

CARICA (orizzontale) NON conosce i formati verticali (IMDF, export, ...). Quando
il sink ``carica_arcgis`` riceve un artefatto upstream, chiede a questo registry
se esiste un adapter capace di trasformarlo in una lista ordinata di
``ArcGISLayerSpec`` (+ renderer per nome layer). I verticali registrano il
proprio adapter al composition root (``app/runtime/services.py``): la direzione
di accoppiamento resta corretta (verticale -> orizzontale), CARICA resta generico.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Protocol, Tuple, runtime_checkable

from app.core.connections.arcgis_writer_boundary import ArcGISLayerSpec
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class MultiLayerArtifact:
    """Esito generico: N layer ordinati pronti per il sink ArcGIS multi-layer.

    ``renderers_by_layer_name`` mappa nome-layer -> ArcGIS ``drawing_info``; gli
    URL per-layer (necessari all'apply) si conoscono solo dopo il write, quindi
    il sink risolve nome->URL a valle.
    """

    layers: Tuple[ArcGISLayerSpec, ...]
    renderers_by_layer_name: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


@runtime_checkable
class MultiLayerArtifactAdapter(Protocol):
    """Adapter di un formato verticale verso il payload multi-layer generico."""

    def matches(self, payload: Mapping[str, object]) -> bool:
        """True se questo adapter riconosce il payload (es. per ``schema``)."""
        ...

    def to_multilayer(self, payload: Mapping[str, object]) -> MultiLayerArtifact:
        """Trasforma il payload riconosciuto in layer + renderer generici."""
        ...


_ADAPTERS: List[MultiLayerArtifactAdapter] = []


def register_multilayer_adapter(adapter: MultiLayerArtifactAdapter) -> None:
    """Registra un adapter (idempotente per tipo: evita duplicati allo stesso tipo)."""
    for existing in _ADAPTERS:
        if type(existing) is type(adapter):
            return
    _ADAPTERS.append(adapter)
    logger.debug(f"multilayer adapter registrato: {type(adapter).__name__}")


def registered_multilayer_adapters() -> Tuple[MultiLayerArtifactAdapter, ...]:
    return tuple(_ADAPTERS)


def resolve_multilayer_artifact(payload: Mapping[str, object]) -> Optional[MultiLayerArtifact]:
    """Ritorna il primo adapter che riconosce il payload, o None.

    ``payload`` e' il JSON top-level dell'artefatto upstream gia' parsato dal
    chiamante. Artefatti non-JSON / non riconosciuti -> None (il sink ricade sul
    path mono-layer/dataframe).
    """
    for adapter in _ADAPTERS:
        try:
            if adapter.matches(payload):
                return adapter.to_multilayer(payload)
        except Exception as exc:  # pragma: no cover - difensivo sul boundary plugin
            logger.warning(
                f"[GESTITO] adapter {type(adapter).__name__} fallito su matches/to_multilayer: {exc}"
            )
    return None


__all__ = [
    "MultiLayerArtifact",
    "MultiLayerArtifactAdapter",
    "register_multilayer_adapter",
    "registered_multilayer_adapters",
    "resolve_multilayer_artifact",
]
