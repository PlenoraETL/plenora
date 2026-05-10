"""
Default in-process resource registry.

"""

from __future__ import annotations

from threading import Lock
from typing import Dict, Optional

from app.core.resources.loaders.file_source_blob import FileSourceBlobLoader
from app.core.resources.loaders.result_blob import ResultBlobLoader
from app.core.resources.types import (
    OutputMode,
    Resource,
    ResourceKind,
    ResourceLoader,
    ResourceRef,
)


class _DefaultResourceRegistry:
    """In-process dispatcher. Implements the `ResourceRegistry`
    Protocol via duck typing (no inheritance)."""

    def __init__(self) -> None:
        self._loaders: Dict[ResourceKind, ResourceLoader] = {}

    def register(self, loader: ResourceLoader) -> None:
        kind = loader.kind
        if kind in self._loaders:
            raise ValueError(
                f"_DefaultResourceRegistry: kind {kind!r} already "
                f"registered (existing={type(self._loaders[kind]).__name__}, "
                f"attempted={type(loader).__name__}). Re-registration is "
                "rejected to prevent silent shadowing."
            )
        self._loaders[kind] = loader

    def get(self, ref: ResourceRef, *, mode: OutputMode) -> Resource:
        try:
            loader = self._loaders[ref.kind]
        except KeyError:
            raise KeyError(
                f"_DefaultResourceRegistry: no loader for kind={ref.kind!r}. "
                f"Registered: {sorted(self._loaders.keys())}. Add via "
                "`registry.register(loader)` at app bootstrap."
            ) from None
        return loader.load(ref, mode=mode)


# Module-level singleton + builder. Tests that need isolation
# build their own via `build_default_registry`
_REGISTRY_LOCK = Lock()
_REGISTRY: Optional[_DefaultResourceRegistry] = None


def build_default_registry() -> _DefaultResourceRegistry:
    """Construct a fresh registry pre-populated with the v1 loaders.

    v1 ships two loaders:
      - ``ResultBlobLoader`` (RESOURCE-ENGINE-002 pilot);
      - ``FileSourceBlobLoader`` (RESOURCE-ENGINE-020, second
        real loader graduating the pilot).

    Other kinds (`intermediate_artifact`, `eseguibili_output`,
    `imdf_bundle`) join only after their tenant binding is
    plumbed through (audit sec. 5/7 of
    `BE_RESOURCE_ENGINE_AUDIT.md`).

    Used directly by tests that want isolation. Production code
    consumes the module-level singleton via `default_registry`.
    """
    reg = _DefaultResourceRegistry()
    reg.register(ResultBlobLoader())
    reg.register(FileSourceBlobLoader())
    return reg


def default_registry() -> _DefaultResourceRegistry:
    """Lazy module-level singleton. Thread-safe init."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = build_default_registry()
    return _REGISTRY


__all__ = [
    "build_default_registry",
    "default_registry",
]
