"""Typed result contracts for ArcGIS provider compatibility boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from app.shared.contracts.payloads import JsonRows, ObjectMap


@dataclass(frozen=True)
class FolderEnsureResult:
    """Result of finding or creating a portal user folder."""

    folder_id: Optional[str]
    title: str
    created: bool = False

    def to_compat_id(self) -> Optional[str]:
        """Return the public ``ensure_folder`` folder-id shape."""
        return self.folder_id


@dataclass(frozen=True)
class ItemListingResult:
    """Result of listing portal items while traversing user folders."""

    items: Tuple[Mapping[str, object], ...]

    def to_compat_list(self) -> JsonRows:
        """Return the public list of raw portal item dictionaries."""
        return [dict(item) for item in self.items]


@dataclass(frozen=True)
class ItemSearchResult:
    """Result of a paginated portal item search."""

    items: Tuple[Mapping[str, object], ...]

    def to_compat_list(self) -> JsonRows:
        """Return the public list of raw search result dictionaries."""
        return [dict(item) for item in self.items]


@dataclass(frozen=True)
class RendererUpdateResult:
    """Result of an ArcGIS renderer updateDefinition call."""

    success: bool
    message: str

    def to_compat_dict(self) -> ObjectMap:
        """Return the public ``{success, message}`` provider shape."""
        return {"success": self.success, "message": self.message}


@dataclass(frozen=True)
class ItemDeleteResult:
    """Result of deleting one portal item."""

    success: bool
    message: str
    item_id: str
    response: Optional[Mapping[str, object]] = None

    def to_compat_dict(self) -> ObjectMap:
        """Return the public item delete dictionary shape."""
        result = {
            "success": self.success,
            "message": self.message,
            "item_id": self.item_id,
        }
        if self.response is not None:
            result["response"] = dict(self.response)
        return result

    @classmethod
    def from_compat_dict(cls, result: Mapping[str, object], item_id: str) -> "ItemDeleteResult":
        """Coerce a compatibility delete dict into the typed result contract."""
        response = result.get("response")
        return cls(
            success=bool(result.get("success")),
            message=str(result.get("message") or ""),
            item_id=str(result.get("item_id") or item_id),
            response=response if isinstance(response, Mapping) else None,
        )


@dataclass(frozen=True)
class DeleteFeatureServiceItemsResult:
    """Counters for deleting a set of discovered Feature Service items."""

    attempted: int
    succeeded: int

    def to_compat_tuple(self) -> tuple[int, int]:
        """Return the public ``(attempted, succeeded)`` tuple shape."""
        return self.attempted, self.succeeded


@dataclass(frozen=True)
class ServiceNameAvailabilityResult:
    """Result of checking ArcGIS portal Feature Service name availability."""

    available: bool
    error: Optional[str] = None

    def to_compat_tuple(self) -> tuple[bool, Optional[str]]:
        """Return the public ``(available, error)`` tuple shape."""
        return self.available, self.error


__all__ = [
    "DeleteFeatureServiceItemsResult",
    "FolderEnsureResult",
    "ItemDeleteResult",
    "ItemListingResult",
    "ItemSearchResult",
    "RendererUpdateResult",
    "ServiceNameAvailabilityResult",
]
