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
    usa `/vsimem` su Linux; su Windows usa una temp dir privata interna al
    boundary, perche' pyogrio e ctypes GDAL possono avere namespace VSI
    distinti. Entrambi i path hanno cleanup deterministico.

Strategia generale:
  - LETTURA: `read_vector_bytes(data, ...)` per qualunque formato
    vector supportato da OGR.
  - SCRITTURA: `write_vector_bytes(gdf, driver=...)` per formati
    single-file; `write_shapefile_zip_bytes(gdf, name)` per SHP.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Optional

from app.core.io import vsimem as vsimem_module
from app.core.io.policy import is_temp_disk_allowed


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


def read_vector_stream(
    stream: BinaryIO,
    *,
    layer: Optional[str] = None,
    columns: Optional[list[str]] = None,
) -> Any:
    """Read a vector dataset from a seekable stream inside the I/O boundary."""
    import pyogrio

    stream.seek(0)
    try:
        return pyogrio.read_dataframe(stream, layer=layer, columns=columns)
    finally:
        stream.seek(0)


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
    """Serialize a GeoDataFrame as a zipped ESRI Shapefile payload.

    Shapefile e' multi-file (.shp/.shx/.dbf/.prj/.cpg). Su Linux i sidecar
    vengono scritti in `/vsimem/{uuid}/{name}.*`; su Windows in un tempdir
    privato e usa-e-getta dentro questo boundary. Il risultato e' sempre uno
    ZIP in memoria.

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

    if os.name == "nt":
        if not is_temp_disk_allowed():
            raise RuntimeError(
                "Export SHP su Windows richiede temp disk esplicito; "
                "PLENORA_IO_DISK_POLICY=memory_only lo vieta"
            )
        # Su Windows il GDAL caricato da pyogrio e quello raggiungibile via
        # ctypes non condividono necessariamente lo stesso namespace
        # /vsimem. Un tempdir privato e cleanup-deterministico evita ZIP
        # vuoti mantenendo il disk touch dentro il boundary I/O autorizzato.
        with tempfile.TemporaryDirectory(prefix="plenora_shp_") as temp_dir:
            root = Path(temp_dir)
            shp_path = root / f"{name}.shp"
            pyogrio.write_dataframe(gdf, shp_path, driver="ESRI Shapefile")
            zip_buf = BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for sidecar_path in sorted(root.glob(f"{name}.*")):
                    zf.write(sidecar_path, arcname=sidecar_path.name)
            return zip_buf.getvalue()

    with vsimem_module.vsi_temp_dir(prefix="plenora_shp_") as vsi_root:
        shp_vsi_path = f"{vsi_root}/{name}.shp"
        pyogrio.write_dataframe(gdf, shp_vsi_path, driver="ESRI Shapefile")

        # I sidecar pyogrio sono creati nella dir VSI; leggiamoli via
        # `vsi_listdir` + `vsi_read_bytes` e zippiamoli in memoria.
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for sidecar_name in sorted(vsimem_module.vsi_listdir(vsi_root)):
                if not sidecar_name.startswith(f"{name}."):
                    # Defensive: ignora entry estranee (non dovrebbero esistere
                    # in una dir creata ad-hoc).
                    continue
                data = vsimem_module.vsi_read_bytes(f"{vsi_root}/{sidecar_name}")
                zf.writestr(sidecar_name, data)
        return zip_buf.getvalue()


def list_vector_layers(data: bytes) -> list[str]:
    """List layer names available in a multi-layer vector dataset (bytes).

    Utile per GPKG e FileGDB che possono contenere multi layer.
    Equivalente bytes-based del listing layer.
    """
    import pyogrio
    infos = pyogrio.list_layers(data)
    # pyogrio.list_layers ritorna np.ndarray con shape (N, 2) -- (name, geom_type)
    return [str(row[0]) for row in infos]


def list_vector_layers_stream(stream: BinaryIO) -> list[str]:
    """List layer names da file-like seekable, senza copia del payload."""
    import pyogrio

    stream.seek(0)
    try:
        infos = pyogrio.list_layers(stream)
        return [str(row[0]) for row in infos]
    finally:
        stream.seek(0)


__all__ = [
    "list_vector_layers",
    "list_vector_layers_stream",
    "read_vector_bytes",
    "read_vector_stream",
    "write_shapefile_zip_bytes",
    "write_vector_bytes",
]
