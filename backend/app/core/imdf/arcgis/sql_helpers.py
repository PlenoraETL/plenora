"""
Cluster SQL_HELPERS — quoting valori SQL ArcGIS-safe + chunking generico

+ batch-resolution OBJECTID via business-key.
"""

import importlib
from typing import Dict, List, Sequence, Tuple

from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISReadProviderProtocol


def _arcgis_sql_helpers():
    """Lazy import: BE-GO-READY-ARCH-057 forbids `from app.core.features`
    imports inside `app.core.imdf`. Resolved dynamically via importlib."""
    return importlib.import_module("app.core.features.arcgis_sql_helpers")


def _quote_sql_value(v) -> str:
    """Quote un valore per una clausola WHERE SQL.

    Gestisce:
      - None / pd.NA / np.nan / pd.NaT → 'NULL'
      - bool → 1 / 0 (ArcGIS non accetta True/False)
      - int / float / numpy int64/float64 → as-is, convertito a Python native
      - str (incluso numpy str) → single-quoted, apici raddoppiati
      - altri tipi → str(v) + quoting

    La conversione a Python native e' necessaria perche' i GeoDataFrame
    caricati da pyogrio possono avere dtype numpy (int64, float64) che
    `isinstance(v, (int, float))` NON riconosce, con il risultato di passarli
    per il branch string producendo letterali tipo 'np.int64(123)'.
    """
    return _arcgis_sql_helpers().quote_sql_value(v)


def _chunked(seq, size):
    """Yield chunks successivi di al massimo `size` elementi.

    Raises:
        ValueError: se size <= 0 (eviterebbe un infinite loop con buf
            che non raggiunge mai la condizione `len(buf) >= size`).
    """
    yield from _arcgis_sql_helpers().chunked(seq, size)


def _query_oid_by_key(
    client: ArcGISReadProviderProtocol,
    conn: ArcGISConnectionInfo,
    layer_url: str,
    match_col: str,
    values: Sequence[object],
) -> Tuple[Dict[str, int], List[str]]:
    """Costruisce un dict {str(match_val): OBJECTID} interrogando il FS per
    le features con `match_col IN (values)`. Batcha la query su WHERE da 200.

    Se una o piu' , `oid_by_key` ritorna PARZIALE: il caller
    deve controllare `errors` (non-vuoto → parziale) e decidere se abortire
    o procedere. In caso di parziale viene loggato un warning con il conteggio.

    Returns (oid_by_key, errors).

    Thin IMDF adapter for publish call sites. ArcGIS mechanics belong in the
    canonical provider method.
    """
    return _arcgis_sql_helpers().query_oid_by_key(
        client,
        conn,
        layer_url,
        match_col,
        values,
    )
