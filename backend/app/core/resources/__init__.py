"""
Resource Engine — capability registry, NOT god-engine.

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
