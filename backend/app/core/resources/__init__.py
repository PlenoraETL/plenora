"""
Resource Engine — capability registry, NOT god-engine.

Canonical usage:

    resource = default_registry.get(ref, mode="bytes")

Bytes mode is the default for new consumers. Path mode is reserved for
downstream libraries that structurally require a filesystem path.
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
