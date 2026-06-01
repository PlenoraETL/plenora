"""Typed ArcGIS write column mapping contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from app.core.dataframes.ports import FrameLike
from app.core.dataframes.tabular import select_and_rename_columns
from app.shared.execution.mapping_rules import check_mapping

# Prefissi storici dei ValueError ArcGIS, per codice Issue (backward-compat).
_ARCGIS_ERR_PREFIX = {
    "mapping.dup_source": "column_mappings ArcGIS contiene sorgenti duplicate: ",
    "mapping.dup_dest": "column_mappings ArcGIS contiene destinazioni duplicate: ",
    "mapping.source_missing": "column_mappings ArcGIS riferimento colonne sorgente mancanti: ",
}


@dataclass(frozen=True)
class ArcGISColumnMapping:
    """One source DataFrame column mapped to one ArcGIS field name."""

    source_column: str
    dest_column: str
    is_geometry: bool = False
    skip: bool = False


@dataclass(frozen=True)
class ArcGISMappedFrame:
    """DataFrame plus the geometry column name after write-boundary mapping."""

    df: FrameLike
    geometry_column: str


@dataclass(frozen=True)
class ArcGISWriteMappingContract:
    """Column mapping applied only at the ArcGIS write serialization boundary."""

    mappings: tuple[ArcGISColumnMapping, ...] = ()

    @classmethod
    def from_raw(cls, raw_mappings: object) -> "ArcGISWriteMappingContract":
        """Build the typed contract from persisted canvas mapping payloads."""
        if raw_mappings is None:
            return cls()
        if not isinstance(raw_mappings, Sequence) or isinstance(raw_mappings, (str, bytes)):
            raise ValueError("column_mappings ArcGIS deve essere una lista")

        mappings: list[ArcGISColumnMapping] = []
        for raw_mapping in raw_mappings:
            if isinstance(raw_mapping, ArcGISColumnMapping):
                mappings.append(raw_mapping)
                continue
            if not isinstance(raw_mapping, Mapping):
                raise ValueError("column_mappings ArcGIS contiene una voce non valida")

            source = _string_value(raw_mapping, "source_column") or _string_value(raw_mapping, "source")
            dest = _string_value(raw_mapping, "dest_column") or _string_value(raw_mapping, "dest")
            skip = raw_mapping.get("skip") is True
            is_geometry = raw_mapping.get("is_geometry") is True
            mappings.append(
                ArcGISColumnMapping(
                    source_column=source,
                    dest_column=dest,
                    is_geometry=is_geometry,
                    skip=skip,
                )
            )

        return cls(tuple(mappings))

    @property
    def has_active_mappings(self) -> bool:
        return any(
            mapping.source_column and mapping.dest_column and not mapping.skip
            for mapping in self.mappings
        )

    def apply(
        self,
        df: FrameLike,
        *,
        geometry_column: str,
        required_match_column: str = "",
    ) -> ArcGISMappedFrame:
        """Return a mapped copy without mutating the upstream DataFrame."""
        if not self.has_active_mappings:
            return ArcGISMappedFrame(df=df, geometry_column=geometry_column)

        active = tuple(
            mapping
            for mapping in self.mappings
            if mapping.source_column and mapping.dest_column and not mapping.skip
        )
        # Validazione (dup-source / dup-dest / source-missing) via regola pura
        # condivisa; rilancia il ValueError storico dal primo Issue (ordine
        # preservato da check_mapping). Il check match-column resta post-mapping.
        issues = check_mapping(set(df.columns), active)
        if issues:
            first = issues[0]
            cols = list(first.suggestion["columns"]) if first.suggestion else []
            raise ValueError(_ARCGIS_ERR_PREFIX[first.code] + ", ".join(cols))

        sources = [mapping.source_column for mapping in active]
        selected_columns = list(sources)
        if geometry_column in df.columns and geometry_column not in selected_columns:
            selected_columns.append(geometry_column)

        rename_map = {mapping.source_column: mapping.dest_column for mapping in active}
        mapped_geometry_column = geometry_column
        for mapping in active:
            if mapping.is_geometry or mapping.source_column == geometry_column:
                mapped_geometry_column = mapping.dest_column
                break

        mapped_df = select_and_rename_columns(
            df,
            selected_columns,
            rename_map=rename_map,
        )
        if mapped_geometry_column != geometry_column and hasattr(mapped_df, "set_geometry"):
            mapped_df = mapped_df.set_geometry(mapped_geometry_column)

        if required_match_column and required_match_column not in mapped_df.columns:
            raise ValueError(
                "column_mappings ArcGIS non produce la colonna match richiesta: "
                f"{required_match_column}"
            )

        return ArcGISMappedFrame(df=mapped_df, geometry_column=mapped_geometry_column)


def _string_value(mapping: Mapping[object, object], key: str) -> str:
    value = mapping.get(key)
    return value.strip() if isinstance(value, str) else ""
