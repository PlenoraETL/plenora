"""Provider-facing ArcGIS feature writer boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Protocol, Sequence, Union

from app.core.connections.arcgis_write_mapping import (
    ArcGISColumnMapping,
    ArcGISWriteMappingContract,
)
from app.core.dataframes.ports import FrameLike


ArcGISRawColumnMapping = Union[ArcGISColumnMapping, Mapping[str, object]]
ArcGISColumnMappingsInput = Optional[
    Union[ArcGISWriteMappingContract, Sequence[ArcGISRawColumnMapping]]
]
ArcGISFeatureWritePublicResult = Mapping[str, object]


@dataclass(frozen=True)
class ArcGISLayerSpec:
    """Un layer da pubblicare in un Feature Service multi-layer.

    Domain-neutral: descrive solo `cosa` pubblicare (nome + dati + colonna
    geometria), senza alcuna conoscenza del dominio chiamante (IMDF, export,
    ...). Il chiamante decide quali layer, in quale ordine, e l'eventuale
    stile (applicato in un passo separato)."""

    layer_name: str
    df: FrameLike
    geometry_column: str = "geometry"


class ArcGISFeatureWriter(Protocol):
    """Generic ArcGIS feature writer boundary consumed by provider callers."""

    def create_service_from_df(
        self,
        connection_id: str,
        service_name: str,
        layer_name: str,
        df: FrameLike,
        geometry_column: str = "geometry",
        source_srid: Optional[int] = None,
        folder_id: str = "",
        column_mappings: ArcGISColumnMappingsInput = None,
    ) -> ArcGISFeatureWritePublicResult:
        """Create a feature service from an in-memory DataFrame."""
        ...

    def write_features_from_df(
        self,
        connection_id: str,
        layer_url: str,
        operation: str,
        df: FrameLike,
        geometry_column: str = "geometry",
        source_srid: Optional[int] = None,
        batch_size: Optional[int] = None,
        match_column: str = "",
        column_mappings: ArcGISColumnMappingsInput = None,
    ) -> ArcGISFeatureWritePublicResult:
        """Write features to an existing feature layer from an in-memory DataFrame."""
        ...

    def create_service_with_layers(
        self,
        connection_id: str,
        service_name: str,
        layers: Sequence[ArcGISLayerSpec],
        source_srid: Optional[int] = None,
        folder_id: str = "",
    ) -> ArcGISFeatureWritePublicResult:
        """Create a Feature Service holding N layers (generic, domain-neutral).

        Layer 0 creates the service; layers 1..N are appended. Returns a mapping
        with ``service_url``, ``item_id`` and a per-layer ``layers`` list. Any
        caller (IMDF, exports, ...) supplies its own ordered layer specs.
        """
        ...


def normalize_arcgis_column_mappings(
    column_mappings: ArcGISColumnMappingsInput,
) -> ArcGISWriteMappingContract:
    """Normalize raw API/canvas payloads at the ArcGIS write boundary."""
    if isinstance(column_mappings, ArcGISWriteMappingContract):
        return column_mappings
    return ArcGISWriteMappingContract.from_raw(column_mappings)
