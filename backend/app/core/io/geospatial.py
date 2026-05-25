"""Geospatial I/O boundary — RAM-only via pyogrio.

Wrapper RAM-only su pyogrio 0.12+ che supporta nativamente `bytes` /
`BytesIO` come `path_or_buffer`. Internamente pyogrio gestisce
`/vsimem` per i sidecar Shapefile multi-file e `/vsizip` per gli
archivi: il caller di questo modulo lavora SOLO con `bytes`, non
deve mai costruire path temp.

Driver supportati (verificati in container plenora-backend con
GDAL 3.13.0):
  - GeoJSON: single-file, bytes nativi sia in read che write
  - GPKG: single-file SQLite, bytes nativi sia in read che write
  - FlatGeobuf: single-file, bytes nativi (formato consigliato per
    nuovi flussi RAM-only, piu' veloce di GPKG su read)
  - ESRI Shapefile (read-only via ZIP bytes): pyogrio rileva
    automaticamente lo shapefile dentro lo ZIP via `/vsizip`
  - FileGDB (read-only via ZIP bytes): idem

Limite corrente:
  Lo Shapefile WRITE-to-ZIP-bytes richiede leggere i sidecar da
  `/vsimem` (`.shp/.shx/.dbf/.prj/.cpg`) per costruire lo ZIP, e
  pyogrio non espone una `vsi_read` API. Per ora il write di
  Shapefile-ZIP resta fuori scope; i caller che hanno bisogno di
  Shapefile output dovrebbero migrare a GPKG (single-file, nessun
  problema multi-file).

Strategia generale:
  - LETTURA: `read_vector_bytes(data, ...)` per qualunque formato
    vector supportato da OGR.
  - SCRITTURA: `write_vector_bytes(gdf, driver=...)` per formati
    single-file. Shapefile-ZIP non supportato.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, Optional


def read_vector_bytes(
    data: bytes,
    *,
    layer: Optional[str] = None,
    columns: Optional[list[str]] = None,
) -> Any:
    """Read a vector dataset from raw bytes, returning a GeoDataFrame.

    Supporta tutti i formati vector che pyogrio/GDAL conosce:
      - single-file: GeoJSON, GPKG, FlatGeobuf
      - ZIP: shapefile multi-file in ZIP, FileGDB in ZIP (rilevati
        automaticamente da pyogrio via /vsizip)

    Args:
        data: bytes raw del dataset (single-file payload o ZIP).
        layer: nome layer (necessario per GPKG/FileGDB con >1 layer;
            opzionale per single-layer).
        columns: subset di colonne da caricare (perf).

    Returns:
        `geopandas.GeoDataFrame` con il layer richiesto.

    Raises:
        `pyogrio.errors.DataSourceError`: bytes corrotti o formato
            non riconosciuto da OGR.
    """
    import pyogrio
    return pyogrio.read_dataframe(data, layer=layer, columns=columns)


def write_vector_bytes(
    gdf: Any,
    *,
    driver: str = "GPKG",
    layer: Optional[str] = None,
) -> bytes:
    """Serialize a GeoDataFrame to bytes via pyogrio.

    Solo formati single-file (no multi-file Shapefile).

    Args:
        gdf: GeoDataFrame da serializzare.
        driver: OGR driver name. Default `GPKG`. Alternative:
            `GeoJSON`, `FlatGeobuf`.
        layer: nome layer interno (per GPKG con piu' layer; default
            usa il nome dataset).

    Returns:
        `bytes` del payload pronto per object storage / DB BYTEA /
        StreamingResponse.

    Raises:
        `ValueError`: se `driver` punta a un formato multi-file
            (ESRI Shapefile, OpenFileGDB) — non supportato RAM-only.
    """
    if driver in ("ESRI Shapefile", "OpenFileGDB"):
        raise ValueError(
            f"Driver {driver!r} e' multi-file: usa write a path "
            f"+ ZIP manuale, oppure migra a GPKG/FlatGeobuf."
        )
    import pyogrio
    buf = BytesIO()
    if layer is not None:
        pyogrio.write_dataframe(gdf, buf, driver=driver, layer=layer)
    else:
        pyogrio.write_dataframe(gdf, buf, driver=driver)
    return buf.getvalue()


def list_vector_layers(data: bytes) -> list[str]:
    """List layer names available in a multi-layer vector dataset (bytes).

    Utile per GPKG e FileGDB che possono contenere multi layer.
    Equivalent del vecchio `fiona.listlayers(path)` ma su bytes.
    """
    import pyogrio
    infos = pyogrio.list_layers(data)
    # pyogrio.list_layers ritorna np.ndarray con shape (N, 2) -- (name, geom_type)
    return [str(row[0]) for row in infos]


__all__ = [
    "list_vector_layers",
    "read_vector_bytes",
    "write_vector_bytes",
]
