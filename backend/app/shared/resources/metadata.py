"""
Resource metadata contract - typed envelope for schema and CRS.

BE-GO-READY-ARCH-055-W4-SCHEMA-CRS-METADATA-STRATEGY.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Sequence

SourceKind = Literal["file", "db", "project", "api", "static"]

_GEOMETRY_PREFIX = "geometry"


@dataclass(frozen=True)
class ColumnMeta:
    """Typed descriptor for a single data column.

    ``type`` uses the PLENORA canonical vocabulary:
    ``int``, ``real``, ``str``, ``bool``, ``date``,
    ``geometry``, ``geometry:subtype``, ``geometry:subtype:EPSG``.

    Normalisation helpers live in ``app.shared.column_types``;
    this class stores the raw value as received to avoid a
    cross-module dependency at construction time.
    """

    name: str
    type: str

    def is_geometry(self) -> bool:
        """True if this column carries spatial data."""
        return self.type.startswith(_GEOMETRY_PREFIX)

    def extract_epsg(self) -> Optional[int]:
        """Parse the EPSG integer from a type string like ``geometry:polygon:4326``.

        Returns ``None`` when absent (e.g. bare ``geometry`` or
        ``geometry:point`` without SRID) or when the third component is
        not a valid integer.
        """
        parts = self.type.split(":")
        if len(parts) >= 3:
            try:
                return int(parts[2])
            except ValueError:
                return None
        return None

    def geometry_subtype(self) -> Optional[str]:
        """Return the geometry sub-type (e.g. ``"polygon"``) or ``None``."""
        parts = self.type.split(":")
        if len(parts) >= 2 and parts[0] == _GEOMETRY_PREFIX:
            return parts[1] or None
        return None


@dataclass(frozen=True)
class ResourceMetadata:
    """Immutable metadata envelope that travels with a data resource.

    Replaces the implicit CRS/geometry encoding that today lives inside
    type strings like ``geometry:polygon:4326`` (persisted in
    ``projects.canvas.columns_metadata``).  Explicit fields let callers
    do a plain attribute read instead of parsing a string.

    The envelope is a pure value object: no I/O, no DB, no framework
    imports.  It can be constructed at the point of schema inference and
    passed through the pipeline without re-guessing.
    """

    columns: tuple[ColumnMeta, ...]
    source_kind: SourceKind
    geometry_column: Optional[str] = None
    crs_epsg: Optional[int] = None
    row_count: Optional[int] = None
    group_id: Optional[str] = None
    project_id: Optional[str] = None

    # Factory - interoperates with the existing wire format

    @classmethod
    def from_columns_list(
        cls,
        columns_list: Sequence[dict],
        *,
        source_kind: SourceKind,
        row_count: Optional[int] = None,
        group_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> ResourceMetadata:
        """Build from the existing ``[{"name": str, "type": str}]`` format.

        Extracts ``geometry_column`` and ``crs_epsg`` from the first
        geometry column found; all other columns are preserved as-is.
        Rows missing ``name`` are silently skipped (tolerant of legacy
        canvas state).
        """
        cols: list[ColumnMeta] = []
        for c in columns_list:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            if not name:
                continue
            cols.append(ColumnMeta(name=name, type=c.get("type") or "str"))

        geom_col: Optional[str] = None
        crs_epsg: Optional[int] = None
        for col in cols:
            if col.is_geometry():
                geom_col = col.name
                crs_epsg = col.extract_epsg()
                break

        return cls(
            columns=tuple(cols),
            source_kind=source_kind,
            geometry_column=geom_col,
            crs_epsg=crs_epsg,
            row_count=row_count,
            group_id=group_id,
            project_id=project_id,
        )

    # Serialisation - round-trips cleanly with the existing wire format

    def to_columns_list(self) -> list[dict]:
        """Serialise back to ``[{"name": str, "type": str}]``.

        Suitable for passing to ``save_columns_metadata_with_retry`` or
        writing into ``canvas.columns_metadata`` unchanged.
        """
        return [{"name": col.name, "type": col.type} for col in self.columns]

    # Accessors

    def column_type_map(self) -> dict[str, str]:
        """``{name: type}`` dict for O(1) type lookup.

        Mirrors the output of ``app.shared.column_types.metadata_to_dict``
        so callers can use either interchangeably during migration.
        """
        return {col.name: col.type for col in self.columns}

    def has_geometry(self) -> bool:
        """True if at least one geometry column was found."""
        return self.geometry_column is not None

    def column_names(self) -> tuple[str, ...]:
        return tuple(col.name for col in self.columns)


@dataclass(frozen=True)
class LoadedResource:
    """Typed envelope pairing loaded data with its schema/CRS metadata.

    ``data`` is typed ``Any`` to keep this leaf module free of pandas/
    geopandas imports. In practice it is always a DataFrame or GeoDataFrame
    at runtime. ``metadata`` is ``None`` when the caller did not supply
    column definitions (e.g. static-source branch).
    """

    data: Any
    metadata: Optional[ResourceMetadata] = None
