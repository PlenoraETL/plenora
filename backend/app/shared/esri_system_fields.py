"""
Shared boundary per i campi di sistema ESRI (ArcGIS).

Source-of-truth per i field name che ArcGIS genera/calcola
"""

from __future__ import annotations

from typing import Final


# Field name sets (immutabili)

# Subset auto-generated OID/GUID: ArcGIS assegna OBJECTID e (se abilitato)
# GLOBALID al server-side; inviarli da `addFeatures` causa errori o duplica
# valori. Usato da `arcgis_client.dataframe_to_esri_features` per le
# operazioni `add` / `create_service`.
ESRI_OID_FIELDS_LITERAL: Final[frozenset[str]] = frozenset({
    "OBJECTID",
    "objectid",
    "GLOBALID",
    "globalid",
    "GlobalID",
})

# Set completo dei field name auto-generati o calcolati da ArcGIS,
# exact-match con le casings note osservate sui FileGeoDatabase Esri:
#   - OBJECTID / GLOBALID (varianti casing)
#   - Shape_Length / Shape_Area + varianti SHAPE_*
#   - CREATED_USER / CREATED_DATE / LAST_EDITED_USER / LAST_EDITED_DATE
# Usato dal loader IMDF per filtrare colonne dei GeoDataFrame letti dal
# GDB prima di costruire il payload `addToDefinition`.
ESRI_SYSTEM_FIELDS_LITERAL: Final[frozenset[str]] = ESRI_OID_FIELDS_LITERAL | frozenset({
    "Shape_Length",
    "Shape_Area",
    "SHAPE_Length",
    "SHAPE_Area",
    "CREATED_USER",
    "CREATED_DATE",
    "LAST_EDITED_USER",
    "LAST_EDITED_DATE",
})


# Normalized lookup (helper case-insensitive)

_SYS_NORMALIZED: Final[frozenset[str]] = frozenset({
    "OBJECTID",
    "GLOBALID",
    "SHAPE",
    "SHAPEAREA",
    "SHAPELENGTH",
    "CREATEDUSER",
    "CREATEDDATE",
    "LASTEDITEDUSER",
    "LASTEDITEDDATE",
    "GDBARCHIVEOID",
})


def normalize_field_name(name: str) -> str:
    """Normalizza un field name per il match case-insensitive.

    Coerente bit-per-bit con il pattern pre-IMDF-001 in
    `core/imdf/validator._is_system_field`:
    `(name or "").upper().replace("_", "")`.

    `name=None` ritorna `""` (e quindi non matcha mai).
    """
    return (name or "").upper().replace("_", "")


def is_esri_system_field(name: str) -> bool:
    """True se `name` corrisponde a un campo di sistema ArcGIS dopo
    normalizzazione case-insensitive (uppercase + strip underscore).

    Equivalente bit-per-bit al pre-IMDF-001
    `core/imdf/validator._is_system_field`.
    """
    return normalize_field_name(name) in _SYS_NORMALIZED


__all__ = [
    "ESRI_OID_FIELDS_LITERAL",
    "ESRI_SYSTEM_FIELDS_LITERAL",
    "is_esri_system_field",
    "normalize_field_name",
]
