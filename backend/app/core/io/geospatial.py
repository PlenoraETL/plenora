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
  - ESRI Shapefile WRITE-as-ZIP-bytes: `write_shapefile_zip_bytes`
    usa una temp dir interna al boundary per scrivere i sidecar
    (.shp/.shx/.dbf/.prj/.cpg) e li zippa in bytes. La temp dir
    e' cleanup-deterministico via `TemporaryDirectory`.

Strategia generale:
  - LETTURA: `read_vector_bytes(data, ...)` per qualunque formato
    vector supportato da OGR.
  - SCRITTURA: `write_vector_bytes(gdf, driver=...)` per formati
    single-file; `write_shapefile_zip_bytes(gdf, name)` per SHP.
"""

from __future__ import annotations

import zipfile
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


def write_shapefile_zip_bytes(
    gdf: Any,
    *,
    name: str = "layer",
) -> bytes:
    """Serialize a GeoDataFrame as a zipped ESRI Shapefile (bytes, fully RAM).

    Shapefile e' multi-file (.shp/.shx/.dbf/.prj/.cpg). QUERY-1 step 4:
    sidecar scritti in `/vsimem/{uuid}/{name}.*` via pyogrio (GDAL
    VSI handler), poi letti via `vsi_read_bytes` + assemblati come
    zip in memoria. Zero disk touch.

    Pattern mirror di IMDF-5 (`open_gdb_in_vsimem`): pyogrio scrive
    sul path /vsimem; `app.core.io.vsimem` (ctypes adapter) gestisce
    mkdir/listdir/read/rmdir_recursive contro `libgdal.so` bundled.

    Args:
        gdf: GeoDataFrame da serializzare.
        name: nome base dei file (`.shp`/`.shx`/...); default `layer`.

    Returns:
        `bytes` dello ZIP che contiene i 4-5 sidecar dello Shapefile.
    """
    import pyogrio
    from app.core.io.vsimem import (
        vsi_listdir, vsi_read_bytes, vsi_temp_dir,
    )

    with vsi_temp_dir(prefix="plenora_shp_") as vsi_root:
        shp_vsi_path = f"{vsi_root}/{name}.shp"
        pyogrio.write_dataframe(gdf, shp_vsi_path, driver="ESRI Shapefile")

        # I sidecar pyogrio sono creati nella dir VSI; leggiamoli via
        # `vsi_listdir` + `vsi_read_bytes` e zippiamoli in memoria.
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for sidecar_name in sorted(vsi_listdir(vsi_root)):
                if not sidecar_name.startswith(f"{name}."):
                    # Defensive: ignora entry estranee (non dovrebbero esistere
                    # in una dir creata ad-hoc).
                    continue
                data = vsi_read_bytes(f"{vsi_root}/{sidecar_name}")
                zf.writestr(sidecar_name, data)
        return zip_buf.getvalue()


def list_vector_layers(data: bytes) -> list[str]:
    """List layer names available in a multi-layer vector dataset (bytes).

    Utile per GPKG e FileGDB che possono contenere multi layer.
    Equivalent del vecchio layer listing path-based ma su bytes.
    """
    import pyogrio
    infos = pyogrio.list_layers(data)
    # pyogrio.list_layers ritorna np.ndarray con shape (N, 2) -- (name, geom_type)
    return [str(row[0]) for row in infos]


__all__ = [
    "list_vector_layers",
    "read_vector_bytes",
    "write_shapefile_zip_bytes",
    "write_vector_bytes",
]
