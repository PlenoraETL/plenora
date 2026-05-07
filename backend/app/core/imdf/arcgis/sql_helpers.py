"""
Cluster SQL_HELPERS — quoting valori SQL ArcGIS-safe + chunking generico

+ batch-resolution OBJECTID via business-key.
"""

from typing import Dict, List, Tuple

from app.utils.logger import get_logger

logger = get_logger("app.core.imdf.arcgis_uploader")


def _quote_sql_value(v) -> str:
    """Quote un valore per una clausola WHERE SQL.

    Gestisce:
      - None / pd.NA / np.nan / pd.NaT   → 'NULL'
      - bool                              → 1 / 0 (ArcGIS non accetta True/False)
      - int / float / numpy int64/float64 → as-is, convertito a Python native
      - str (incluso numpy str)           → single-quoted, apici raddoppiati
      - altri tipi                        → str(v) + quoting

    La conversione a Python native e' necessaria perche' i GeoDataFrame
    caricati da pyogrio possono avere dtype numpy (int64, float64) che
    `isinstance(v, (int, float))` NON riconosce, con il risultato di passarli
    per il branch string producendo letterali tipo 'np.int64(123)'.
    """
    # NULL (incluso pd.NA / np.nan / pd.NaT)
    try:
        import pandas as _pd
        if v is None or _pd.isna(v):
            return "NULL"
    except (TypeError, ValueError):
        # pd.isna solleva su tipi non comparabili (es. tuple). Fallback:
        if v is None:
            return "NULL"

    # bool (prima di int, perche' True/False sono int in Python)
    # Gestisce anche numpy.bool_ che NON e' sottoclasse di Python bool.
    try:
        import numpy as _np
        if isinstance(v, (bool, _np.bool_)):
            return "1" if bool(v) else "0"
        if isinstance(v, _np.integer):
            return str(int(v))
        if isinstance(v, _np.floating):
            f = float(v)
            if f != f:  # NaN
                return "NULL"
            return repr(f)
    except ImportError:
        if isinstance(v, bool):
            return "1" if v else "0"

    # Integer / float Python nativi
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return "NULL" if v != v else repr(v)

    # Stringhe e tutto il resto
    s = str(v).replace("'", "''")
    return f"'{s}'"


def _chunked(seq, size):
    """Yield chunks successivi di al massimo `size` elementi.

    Raises:
        ValueError: se size <= 0 (eviterebbe un infinite loop con buf
            che non raggiunge mai la condizione `len(buf) >= size`).
    """
    if size <= 0:
        raise ValueError(f"_chunked: size deve essere > 0, ricevuto {size}")
    buf = []
    for item in seq:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def _query_oid_by_key(
    client, conn, layer_url: str, match_col: str, values: list,
) -> Tuple[Dict[str, int], List[str]]:
    """Costruisce un dict {str(match_val): OBJECTID} interrogando il FS per
    le features con `match_col IN (values)`. Batcha la query su WHERE da 200.

    Se una o piu' batch falliscono, `oid_by_key` ritorna PARZIALE: il caller
    deve controllare `errors` (non-vuoto → parziale) e decidere se abortire
    o procedere. In caso di parziale viene loggato un warning con il conteggio.

    Returns (oid_by_key, errors).
    """
    token = client.authenticate(conn)
    referer = {"Referer": conn.server_url}
    oid_by_key: Dict[str, int] = {}
    errors: List[str] = []
    expected = len(values)
    batches_ok = 0
    batches_failed = 0

    for batch in _chunked(values, 200):
        where = f"{match_col} IN ({','.join(_quote_sql_value(v) for v in batch)})"
        params = {
            "where": where,
            "outFields": f"OBJECTID,{match_col}",
            "returnGeometry": "false",
            "f": "json",
        }
        if token:
            params["token"] = token
        try:
            r = client._http.get(f"{layer_url}/query", params=params, timeout=30, headers=referer)
            data = r.json()
            for feat in (data.get("features") or []):
                a = feat.get("attributes") or {}
                key = a.get(match_col)
                oid = a.get("OBJECTID") or a.get("objectid")
                if key is not None and oid is not None:
                    oid_by_key[str(key)] = int(oid)
            batches_ok += 1
        except Exception as e:
            batches_failed += 1
            errors.append(f"query OBJECTID batch {batches_ok + batches_failed}: {e}")

    if errors or (len(oid_by_key) < expected):
        logger.warning(
            f"_query_oid_by_key({match_col}): expected {expected} values, "
            f"resolved {len(oid_by_key)} OID. Batches: {batches_ok} ok, "
            f"{batches_failed} failed. Errors: {len(errors)}"
        )
    return oid_by_key, errors
