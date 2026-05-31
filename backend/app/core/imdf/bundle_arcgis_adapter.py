"""Adapter IMDF: ``plenora.indoor.bundle`` -> payload multi-layer generico.

Questo e' l'unico punto in cui la conoscenza del formato IMDF incontra il sink
ArcGIS orizzontale. Il bundle e' self-describing: il descrittore dichiara ordine
layer, ``identity`` (match_columns), e ``renderer_ref`` (chiave dentro
``renderers``). L'adapter li traduce in ``ArcGISLayerSpec`` ordinati + renderer
per nome layer. CARICA non importa questo modulo: lo registra il composition root.
"""

from __future__ import annotations

from typing import Mapping

from app.core.carica.multilayer_artifact_registry import (
    MultiLayerArtifact,
    register_multilayer_adapter,
)
from app.core.connections.arcgis_writer_boundary import ArcGISLayerSpec
from app.core.imdf import bundle_geo
from app.core.imdf.bundle_loader import BUNDLE_SCHEMA
from app.utils.logger import get_logger

logger = get_logger(__name__)


class IndoorBundleArcGISAdapter:
    """Riconosce un ``plenora.indoor.bundle`` e lo trasforma in N layer ArcGIS."""

    def matches(self, payload: Mapping[str, object]) -> bool:
        return isinstance(payload, Mapping) and payload.get("schema") == BUNDLE_SCHEMA

    def to_multilayer(self, payload: Mapping[str, object]) -> MultiLayerArtifact:
        # build_layer_geodataframes valida l'involucro (parse_indoor_bundle a monte
        # via iter_layer_records) e ritorna {name: {gdf, identity, renderer_ref}}
        # in ordine descrittore.
        entries = bundle_geo.build_layer_geodataframes(dict(payload), drop_z=True)
        renderers_section = payload.get("renderers")
        renderers_by_name: dict[str, Mapping[str, object]] = {}

        specs = []
        for name, entry in entries.items():
            identity = entry.get("identity") or ()
            specs.append(
                ArcGISLayerSpec(
                    layer_name=name,
                    df=entry["gdf"],
                    geometry_column="geometry",
                    match_column=identity[0] if identity else "",
                )
            )
            ref = entry.get("renderer_ref")
            if ref and isinstance(renderers_section, Mapping):
                drawing_info = renderers_section.get(ref)
                if isinstance(drawing_info, Mapping):
                    renderers_by_name[name] = drawing_info

        logger.debug(
            f"IndoorBundleArcGISAdapter: {len(specs)} layer, "
            f"{len(renderers_by_name)} con renderer"
        )
        return MultiLayerArtifact(
            layers=tuple(specs),
            renderers_by_layer_name=renderers_by_name,
        )


def register() -> None:
    """Registra l'adapter IMDF nel registry multi-layer (idempotente)."""
    register_multilayer_adapter(IndoorBundleArcGISAdapter())


__all__ = ["IndoorBundleArcGISAdapter", "register"]
