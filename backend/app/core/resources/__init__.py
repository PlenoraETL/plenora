"""
Resource Engine — capability registry, NOT god-engine.

BE-GO-READY-RESOURCE-ENGINE-002-RESULT-BLOB-LOADER-PILOT.
"""

from app.core.resources.types import (
    OutputMode,
    Resource,
    ResourceKind,
    ResourceLoader,
    ResourceRef,
    ResourceRegistry,
)
from app.core.resources.registry import default_registry, build_default_registry

__all__ = [
    "OutputMode",
    "Resource",
    "ResourceKind",
    "ResourceLoader",
    "ResourceRef",
    "ResourceRegistry",
    "default_registry",
    "build_default_registry",
]
