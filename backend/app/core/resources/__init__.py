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


def build_default_registry():
    """Build the default registry without importing DB-backed loaders at package import."""
    from app.core.resources.registry import build_default_registry as _build

    return _build()


def default_registry():
    """Return the default registry without importing DB-backed loaders at package import."""
    from app.core.resources.registry import default_registry as _default

    return _default()

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
