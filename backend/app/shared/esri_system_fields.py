"""Shared boundary per i campi di sistema ESRI (ArcGIS).

Source-of-truth per i field name che ArcGIS genera/calcola
automaticamente e che NON devono essere inviati in addFeatures /
createService / addToDefinition (l'API ArcGIS rifiuta o duplica).

Storia (BE-IMDF-001, audit BE-IMDF-BOUNDARY-001):
prima dell'estrazione, le definizioni vivevano duplicate in 3 file:

- `core/imdf/loader.py:ESRI_SYSTEM_FIELDS` — set exact-match con
  multiple casings (`OBJECTID`/`objectid`, `Shape_Length`/`SHAPE_Length`,
  ...) usato in `load_imdf_layers` per filtrare colonne da
  `gdf.columns`;
- `core/imdf/validator.py:SYS_NORMALIZED` + `_is_system_field` —
  helper case-insensitive normalizzato (uppercase + strip underscore)
  usato per filtrare i campi del FS ArcGIS esistente;
- `core/features/arcgis_client.py:skip_cols.update({...})` — subset
  con solo le 5 varianti OBJECTID/GLOBALID, usato in
  `dataframe_to_esri_features` per le operazioni `add`/`create_service`.

Ognuna copriva un caso d'uso diverso. La dedup preserva tutti e tre i
contratti senza unificarli in modo lossy:

- `ESRI_SYSTEM_FIELDS_LITERAL` — exact-match casings (loader path).
- `ESRI_OID_FIELDS_LITERAL` — subset 5 varianti OBJECTID/GLOBALID
  (arcgis_client add/create_service path).
- `is_esri_system_field(name)` — normalized lookup helper (validator
  schema-compat path).

Vincoli go-ready:
- modulo pure: zero I/O, zero env reads, zero side effect;
- niente import di pandas/geopandas/requests/ArcGIS clients;
- type hints espliciti, niente `Any`;
- valori `Final[frozenset[str]]` immutabili.
"""

from __future__ import annotations

from typing import Final


# ---------------------------------------------------------------------------
# Field name sets (immutabili)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Normalized lookup (helper case-insensitive)
# ---------------------------------------------------------------------------

# Set normalizzato (uppercase, senza underscore) per il match
# case-insensitive del validator schema-compat. La normalizzazione
# `name.upper().replace("_", "")` permette di matchare sia le varianti
# Esri classiche (`Shape_Area`) sia quelle del nuovo hosted FS
# (`Shape__Area` con doppio underscore) sia il GDB internal
# `GDBArchiveOID`.
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
