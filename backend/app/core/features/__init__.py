"""
Modulo Features — Connettore ArcGIS Feature Service per PLENORA-ETL.

Fornisce lettura/scrittura bidirezionale verso ArcGIS Server, Enterprise e Online
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.features.features_service import FeaturesService

__all__ = [
    "FeaturesService",
    "create_features_service",
]


def __getattr__(name: str) -> object:
    """Keep the public package export without eager worker bootstrap imports."""
    if name in __all__:
        from app.core.features.features_service import (
            FeaturesService,
            create_features_service,
        )

        exports = {
            "FeaturesService": FeaturesService,
            "create_features_service": create_features_service,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
