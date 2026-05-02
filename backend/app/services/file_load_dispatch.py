"""Parser dispatch per file source (STRUCT-001 Seam D-B).

Estratto da `FileSourceService._load_by_format` + duplicato di
`file_preview_temp._load_by_format` (ora rimosso) per eliminare la
duplicazione 1:1 introdotta da Seam D-A.

API go-ready:
    load_by_format(loader, file_path, format_type, layer=None, sheet=None, *, logger)
        -> tuple[pd.DataFrame, object | None]

Tutti i collaborator sono espliciti come argomenti (loader: DataLoaderProtocol,
logger: LoggerProtocol-like). Modulo leaf: zero import da
`app.services.file_service` o `app.services.file_preview_temp`.

Format supportati (mantenuti 1:1 col legacy):
  - csv, xlsx, json -> tabular via loader.load_csv/load_excel/load_json
  - geojson, gpkg, shp, gdb -> geospatial via loader.load_geospatial
                               + conversione `geometry` colonna in WKT.

Format non in lista -> ValueError esplicito (parser non disponibile).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Tuple

import pandas as pd


_TABULAR_FORMATS = ("csv", "xlsx", "json")
_GEOSPATIAL_FORMATS = ("geojson", "gpkg", "shp", "gdb")


def load_by_format(
    loader,
    file_path: Path,
    format_type: str,
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
    *,
    logger,
) -> Tuple[pd.DataFrame, Optional[Any]]:
    """Carica dati in base al formato.

    Args:
        loader:      DataLoaderProtocol-like (load_csv/load_excel/load_json/
                     load_geospatial).
        file_path:   Path del file da caricare (tipicamente un temp file).
        format_type: 'csv'|'xlsx'|'json'|'geojson'|'gpkg'|'shp'|'gdb'.
        layer:       layer geo-spaziale (se applicabile).
        sheet:       foglio Excel (se applicabile).
        logger:      LoggerProtocol-like (info/warning/error/debug). Logger
                     per il messaggio post-load di righe/colonne.

    Returns:
        (df, gdf): DataFrame caricato; gdf None per i formati tabular,
        GeoDataFrame per i geo-spaziali (con `geometry` convertita a WKT).

    Raises:
        ValueError: format_type non riconosciuto.
    """
    gdf = None

    if format_type == 'csv':
        df = loader.load_csv(file_path)
    elif format_type == 'xlsx':
        df = loader.load_excel(file_path, sheet)
    elif format_type == 'json':
        df = loader.load_json(file_path)
    elif format_type in _GEOSPATIAL_FORMATS:
        gdf = loader.load_geospatial(file_path, layer)
        df = pd.DataFrame(gdf)
        if 'geometry' in df.columns:
            df['geometry'] = gdf.geometry.apply(lambda g: g.wkt if g else None)
    else:
        raise ValueError(f"Formato '{format_type}' non supportato")

    logger.info(f"Caricato: {len(df)} righe, {len(df.columns)} colonne")
    return df, gdf
