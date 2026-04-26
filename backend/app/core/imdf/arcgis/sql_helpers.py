"""
Cluster SQL_HELPERS — quoting valori SQL ArcGIS-safe + chunking generico
+ batch-resolution OBJECTID via business-key.

Origine
=======
Estratto da `app.core.imdf.arcgis_uploader` come parte di Phase 5
Batch B4.1 (cluster split, vedi
`orchestrate/phase_5_b4/preflight.md` §6 + §6.1 facade pattern
ratificato in B1).

Razionale del raggruppamento: tutte le entita' qui dentro condividono
il dominio "preparazione query SQL ArcGIS":
  - `_quote_sql_value`   — type-aware quoting per WHERE clause SQL,
                            con gestione esplicita di numpy dtypes
                            (int64/float64/bool_) provenienti da
                            GeoDataFrame caricati via pyogrio.
  - `_chunked`           — generic batch generator usato per spezzare
                            le liste di business-key in WHERE IN (...)
                            da 200 valori.
  - `_query_oid_by_key`  — orchestrator che usa entrambi i precedenti
                            per risolvere `{match_val: OBJECTID}` su
                            un Feature Service ArcGIS.

`_query_oid_by_key` chiama `_quote_sql_value` e `_chunked` come
funzioni module-level — risoluzione per name lookup nello stesso
modulo, NESSUNA dipendenza esterna verso `arcgis_uploader.py`.

Invarianti pubbliche preservate (regola §6.1 zero call-site changes):
  - `from app.core.imdf.arcgis_uploader import _quote_sql_value`
    continua a funzionare grazie al facade re-export in
    `arcgis_uploader.py`. Stesso per `_chunked` e `_query_oid_by_key`.
  - `validator.py` lazy-import `_quote_sql_value` + `_chunked` da
    `app.core.imdf.arcgis_uploader` continua a funzionare.
  - `tests2/shared/test_hypothesis_property_contract.py:16` con
    `from app.core.imdf.arcgis_uploader import _quote_sql_value`
    (property test Hypothesis) continua a funzionare.
  - Nuovo path canonico `from app.core.imdf.arcgis.sql_helpers import
    <fn>` disponibile per future referenze interne al package arcgis.

Logger naming
=============
Logger creato esplicitamente come
`"app.core.imdf.arcgis_uploader"` (NON `__name__`) per preservare il
contratto osservabile pre-extraction: il record warning emesso da
`_query_oid_by_key` (`_query_oid_by_key({match_col}): expected ...`)
continua ad apparire sotto il logger name dei consumer. Stesso
pattern dei moduli figli estratti in B1 (vedi
`transforms/joins.py`, `transforms/security.py`, ecc.).

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`arcgis_uploader.py` al momento di B4.1. Nessuna modifica di logica,
comment, log payload, ordine di operazione, type-handling numpy,
batch-size 200, partial-result semantics di `_query_oid_by_key`.
"""

from typing import Dict, List, Tuple

from app.utils.logger import get_logger

# Stesso logger name di `arcgis_uploader.py`: i record sono
# indistinguibili dai consumer che attaccano handler / filtrano per
# "app.core.imdf.arcgis_uploader" (pattern §6.1 facade).
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
