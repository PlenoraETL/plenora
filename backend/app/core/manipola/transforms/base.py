"""
Operazioni di trasformazione base (non-geospaziali) per MANIPOLA.

Architettura
============
Questo modulo contiene tutte le funzioni di trasformazione non-geospaziali
registrate nel transforms_registry.json. Ogni funzione è una operazione
atomica che riceve un DataFrame (o lista di DataFrame) e restituisce
un DataFrame trasformato.

Convenzione funzioni:
- Input singolo: op_xxx(df: DataFrame, **kwargs) -> DataFrame
- Input multiplo: op_xxx(dfs: List[DataFrame], **kwargs) -> DataFrame
- Tutti i parametri arrivano come kwargs espliciti (preparati da PluginExecutor)
- Le funzioni NON modificano l'input: lavorano sempre su copie

Catalogo operazioni (~25):
- JOIN: op_join (inner/left/right/full/cross join tra due tabelle)
- FILTRO: op_filter (condizioni su colonne con operatori =, !=, >, <, LIKE, IN, etc.)
- SELEZIONA: op_select_columns (selezione/riordino colonne)
- RINOMINA: op_rename (rinomina colonne)
- CAST: op_cast_type (conversione tipi: int, float, str, bool, date)
- CALCOLA: op_add_column (colonna calcolata con espressioni Python/Pandas)
- CONCAT: op_concat (unione verticale di più DataFrame con allineamento colonne)
- DEDUP: op_deduplicate (rimozione duplicati su colonne selezionate)
- ORDINA: op_sort (ordinamento multi-colonna asc/desc)
- CAMPIONA: op_sample (campionamento casuale o primi N record)
- PIVOT: op_pivot (tabella pivot con indice, colonne, valori, aggregazione)
- MELT: op_melt (unpivot: da colonne a righe)
- SPLIT: op_split_column (divide colonna su separatore)
- MERGE: op_merge_columns (unisce colonne con separatore)
- FILL NULL: op_fill_null (riempimento valori nulli: costante, media, mediana, moda, ffill, bfill)
- REPLACE: op_replace_values (sostituzione valori con regex opzionale)
- GROUP BY: op_group_by (aggregazione con funzioni: count, sum, avg, min, max, first, last)
- COLONNA STATICA: op_add_static_column (aggiunge colonna con valore costante)
- GEOMETRY TO COLUMNS: op_geometry_to_columns (estrae lat/lon/wkt da geometria)
- COLUMNS TO GEOMETRY: op_columns_to_geometry (crea geometria da lat/lon)

Integrazione con PluginExecutor:
Ogni funzione viene importata dinamicamente tramite il path nel registry JSON.
Es: "base.op_join" → app.core.manipola.transforms.base.op_join

Uso diretto (per test):
    from app.core.manipola.transforms.base import op_join
    result = op_join([df1, df2], left_column="id", right_column="id", join_type="inner")
"""

import uuid

import numpy as np
import pandas as pd
import geopandas as gpd
from typing import List, Dict, Any, Optional, Union

from app.utils.logger import get_logger

logger = get_logger(__name__)

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# =============================================================================
# JOIN — Unione tabelle per colonna chiave
# Supporta: inner, left, right, outer, cross. Gestisce suffissi per colonne omonime.
# =============================================================================

# Phase 5 B1.1b + B1.2 cutover: cluster JOINS spostato in
# `app.core.manipola.transforms.joins`. Re-export per preservare i path
# pubblici esistenti:
#   - `from app.core.manipola.transforms.base import op_join`
#       (idem op_union, op_cross_join)
#   - `from app.core.manipola.transforms import op_join` (via __init__)
#   - `transforms_registry.json` entries "base.op_join" / "base.op_union"
#     / "base.op_cross_join" risolte da plugin_executor via
#     `getattr(<base module>, "<func>")`.
# Single-source canonica ora vive in `joins.py`. Vedi anche
# `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1 facade pattern.
from .joins import op_join, op_union, op_cross_join  # noqa: F401  (re-export, consumato via getattr)

# Alias storico per retrocompatibilità: `op_concat` = `op_union`.
# Re-bind dopo l'import facade per mantenere il nome importabile da
# `app.core.manipola.transforms.base` (consumer noto: __init__.py
# `from .base import op_union as op_concat`).
op_concat = op_union


# Phase 5 B1.8 cutover: op_filter moved to `transforms.filtering`
# (re-exported in testa al file via facade).


# Phase 5 B1.5 cutover: cluster FORMULA (op_formula + _validate_formula_ast
# + AST whitelists + `import ast as _ast` locale) spostato in
# `app.core.manipola.transforms.formula`. Re-export in testa al file via
# facade. `import ast as _ast` rimosso da base.py: era usato esclusivamente
# da questo cluster, no consumer altrove.


# Phase 5 B1.4 cutover: op_concat_columns moved to `transforms.columns`
# (re-exported in testa al file via facade).


# Phase 5 B1.7 cutover: op_aggregate + op_sort + op_distinct moved to
# `transforms.aggregation` (re-exported in testa al file via facade).


# Phase 5 B1.4 cutover: op_rename + op_drop_columns moved to
# `transforms.columns` (re-exported in testa al file via facade).


# Phase 5 B1.6 cutover: op_fill_na (+ helper privati
# _is_strict_numeric_series e _coerce_fill_value_for_numeric)
# e op_replace moved to `transforms.cleansing` (re-exported in
# testa al file via facade). I 2 helper privati restano single-source
# in `cleansing.py`: nessun consumer esterno li importa (verificato).


# Phase 5 B1.4 cutover: op_split_column moved to `transforms.columns`
# (re-exported in testa al file via facade).


# =============================================================================
# HASH MD5 (Rilevamento modifiche)
# Calcola hash MD5 di colonne selezionate per change detection e anonimizzazione.
# =============================================================================

def op_md5_hash(
    df: DataFrame,
    columns: List[str],
    output_column: str = "md5_hash",
    normalize: bool = True
) -> DataFrame:
    """
    Crea una colonna hash MD5 da una o più colonne.
    
    Utile per change detection: confrontando l'hash tra due estrazioni
    si identifica rapidamente quali righe sono cambiate.
    
    Args:
        df: DataFrame input
        columns: Lista colonne da includere nell'hash
        output_column: Nome colonna output (default: md5_hash)
        normalize: Se True, applica trim+lowercase prima del calcolo
        
    Returns:
        DataFrame con nuova colonna hash
    """
    import hashlib
    
    if not columns:
        logger.warning("MD5 Hash: nessuna colonna selezionata")
        return df
    
    # Filtra solo colonne esistenti
    valid_columns = [c for c in columns if c in df.columns]
    if not valid_columns:
        logger.warning(f"MD5 Hash: nessuna colonna valida tra {columns}")
        return df
    
    # Ordine alfabetico — deterministico
    valid_columns = sorted(valid_columns)
    
    result = df.copy()
    separator = "\x1f"  # Unit Separator — non appare nei dati reali

    try:
        # Per-column prep vettoriale (pandas-native, non e' l'hotspot).
        # Hotspot storico: il doppio passaggio .agg(join, axis=1) + .map(md5)
        # — due row-iter Python consecutivi. Qui li fondiamo in un singolo
        # list-comp su numpy arrays: zip() itera in C, separator.join/
        # hashlib.md5/encode sono tutti C-impl nativi. Speedup tipico ~10x
        # su 10k righe vs il legacy "agg+map".
        col_arrs: List[np.ndarray] = []
        for col in valid_columns:
            s = result[col]
            if s.dtype == object:
                col_str = s.map(
                    lambda v: "" if pd.isna(v) else (v.wkt if hasattr(v, "wkt") else str(v))
                )
            else:
                col_str = s.astype(object).where(s.notna(), "").astype(str)
                col_str = col_str.where(s.notna(), "")
            if normalize:
                col_str = col_str.str.strip().str.lower()
            col_arrs.append(col_str.to_numpy())

        n = len(result)
        if n == 0:
            result[output_column] = pd.Series([], dtype=object)
        else:
            md5 = hashlib.md5
            out = [
                md5(separator.join(row).encode("utf-8")).hexdigest()
                for row in zip(*col_arrs)
            ]
            result[output_column] = np.array(out, dtype=object)
        logger.info(f"MD5 Hash: {len(valid_columns)} colonne -> {output_column}")
        return result
    except Exception as e:
        logger.error(f"[CRASH] Errore MD5 Hash: {e}", exc_info=True)
        raise


# =============================================================================
# MASCHERAMENTO DATI SENSIBILI
# Nasconde dati sensibili con pattern (es: Mario Rossi → M**** R*****).
# =============================================================================

def _mask_cf(value: str) -> str:
    """Maschera codice fiscale: RSSMRA80A01H501Z → RSS***********01Z"""
    if not value or len(value) < 6:
        return value
    return value[:3] + '*' * (len(value) - 6) + value[-3:]


def _mask_email(value: str) -> str:
    """Maschera email: mario.rossi@gmail.com → m****@gmail.com"""
    if not value or '@' not in value:
        return value
    local, domain = value.rsplit('@', 1)
    if len(local) <= 1:
        masked_local = '*'
    else:
        masked_local = local[0] + '*' * (len(local) - 1)
    return f"{masked_local}@{domain}"


def _mask_phone(value: str) -> str:
    """Maschera telefono: +39 333 1234567 → +39 ***4567"""
    import re
    digits_only = re.sub(r'[^\d+]', '', str(value))
    if len(digits_only) < 6:
        return value
    # Mostra prefisso (primi 3) e ultimi 4
    return digits_only[:3] + '*' * (len(digits_only) - 7) + digits_only[-4:]


def _mask_iban(value: str) -> str:
    """Maschera IBAN: IT60X0542811101000000123456 → IT60********************3456"""
    if not value or len(value) < 8:
        return value
    return value[:4] + '*' * (len(value) - 8) + value[-4:]


def _mask_custom(value: str, chars_start: int = 3, chars_end: int = 3, mask_char: str = '*') -> str:
    """Mascheramento personalizzato con N caratteri visibili inizio/fine"""
    if not value:
        return value
    s = str(value)
    if len(s) <= chars_start + chars_end:
        return s  # Troppo corto per mascherare
    return s[:chars_start] + mask_char * (len(s) - chars_start - chars_end) + s[-chars_end:] if chars_end > 0 else s[:chars_start] + mask_char * (len(s) - chars_start)


# Mappa tipo mascheramento → funzione
MASK_FUNCTIONS = {
    'cf': _mask_cf,
    'email': _mask_email,
    'phone': _mask_phone,
    'iban': _mask_iban,
}


def op_mask_data(
    df: DataFrame,
    maskings: List[Dict[str, Any]],
    overwrite: bool = False
) -> DataFrame:
    """
    Maschera dati sensibili in modo irreversibile.
    
    Args:
        df: DataFrame input
        maskings: Lista di configurazioni mascheramento, ognuna con:
            - column: nome colonna da mascherare
            - mask_type: tipo (cf, email, phone, iban, custom)
            - chars_start: (solo custom) caratteri visibili inizio
            - chars_end: (solo custom) caratteri visibili fine
            - mask_char: (solo custom) carattere di maschera
        overwrite: Se True sovrascrive la colonna, altrimenti crea {col}_masked
        
    Returns:
        DataFrame con colonne mascherate
    """
    if not maskings:
        logger.warning("Mask Data: nessuna configurazione di mascheramento")
        return df
    
    result = df.copy()
    
    for masking in maskings:
        column = masking.get('column', '')
        mask_type = masking.get('mask_type', 'custom')
        
        if not column or column not in result.columns:
            logger.warning(f"Mask Data: colonna '{column}' non trovata, skip")
            continue
        
        # Nome colonna output
        out_col = column if overwrite else f"{column}_masked"
        
        try:
            if mask_type in MASK_FUNCTIONS:
                mask_fn = MASK_FUNCTIONS[mask_type]
                result[out_col] = result[column].astype(str).apply(
                    lambda v: mask_fn(v) if v and v != 'nan' and v != 'None' else v
                )
            elif mask_type == 'custom':
                chars_start = int(masking.get('chars_start', 3))
                chars_end = int(masking.get('chars_end', 3))
                mask_char = masking.get('mask_char', '*')
                result[out_col] = result[column].astype(str).apply(
                    lambda v: _mask_custom(v, chars_start, chars_end, mask_char) if v and v != 'nan' and v != 'None' else v
                )
            else:
                logger.warning(f"Mask Data: tipo '{mask_type}' non supportato per '{column}'")
                continue
            
            logger.info(f"Mask Data: {column} -> {out_col} (tipo: {mask_type})")
            
        # intent: fail-closed all-or-nothing masking — privacy-safe.
        # Se una colonna fallisce, l'intero transform fallisce prima di
        # ritornare un `result` con colonne parzialmente mascherate (leak
        # PII). Il caller executor manipola (batch 5 E1 chiuso) cattura
        # RuntimeError e traduce in error dict del nodo. str(e) tecnico
        # preservato nel logger.error [CRASH] server-side per postmortem.
        except Exception as e:
            logger.error(
                f"[CRASH] Errore mascheramento colonna '{column}': {e}",
                exc_info=True,
            )
            raise RuntimeError(
                f"Mask Data: mascheramento fallito per la colonna '{column}'. "
                f"Operazione interrotta per prevenire leak PII parziale."
            ) from e
    
    return result


# Phase 5 B1.6 cutover: op_type_cast moved to `transforms.cleansing`
# (re-exported in testa al file via facade).


# =============================================================================
# GEOMETRIA DA COORDINATE
# Crea colonna geometry Point da colonne latitudine/longitudine.
# =============================================================================

def op_geo_from_coords(
    df: DataFrame,
    mode: str = "latlon",
    lat_column: str = "",
    lon_column: str = "",
    wkt_column: str = "",
    output_column: str = "geometry",
    crs: str = "EPSG:4326"
) -> DataFrame:
    """
    Crea colonna geometria da coordinate lat/lon o da WKT.
    
    Args:
        df: DataFrame input
        mode: 'latlon' per colonne lat/lon, 'wkt' per colonna WKT
        lat_column: Nome colonna latitudine
        lon_column: Nome colonna longitudine
        wkt_column: Nome colonna WKT
        output_column: Nome colonna geometria output
        crs: Sistema di riferimento (default EPSG:4326)
        
    Returns:
        GeoDataFrame con colonna geometria
    """
    from shapely.geometry import Point
    from shapely import wkt
    
    result = df.copy()
    
    try:
        if mode == 'latlon':
            if not lat_column or lat_column not in result.columns:
                logger.warning(f"Geo from Coords: colonna lat '{lat_column}' non trovata")
                return df
            if not lon_column or lon_column not in result.columns:
                logger.warning(f"Geo from Coords: colonna lon '{lon_column}' non trovata")
                return df
            
            # Converti a numerico
            lats = pd.to_numeric(result[lat_column], errors='coerce')
            lons = pd.to_numeric(result[lon_column], errors='coerce')
            
            # Crea Point (lon, lat) - ordine GIS standard
            geometries = []
            for lat, lon in zip(lats, lons):
                if pd.notna(lat) and pd.notna(lon):
                    geometries.append(Point(lon, lat))
                else:
                    geometries.append(None)
            
            result[output_column] = geometries
            
        elif mode == 'wkt':
            if not wkt_column or wkt_column not in result.columns:
                logger.warning(f"Geo from Coords: colonna WKT '{wkt_column}' non trovata")
                return df
            
            def safe_wkt_parse(val):
                """Parsa una stringa WKT in oggetto Shapely; restituisce None per valori vuoti o malformati."""
                if pd.isna(val) or not val or str(val).strip() == '':
                    return None
                try:
                    return wkt.loads(str(val))
                except Exception as _wkt_err:
                    # Parse WKT fallito per riga singola — debug (row-loop:
                    # warning causerebbe flood su dataset con WKT malformati).
                    logger.debug(f"safe_wkt_parse fallita: {_wkt_err}")
                    return None
            
            result[output_column] = result[wkt_column].apply(safe_wkt_parse)
        
        else:
            logger.warning(f"Geo from Coords: modo '{mode}' non supportato")
            return df
        
        # Converti a GeoDataFrame
        gdf = gpd.GeoDataFrame(result, geometry=output_column, crs=crs)
        
        logger.info(f"Geo from Coords ({mode}): -> {output_column} [{crs}]")
        return gdf
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Geo from Coords: {e}", exc_info=True)
        raise


# Phase 5 B1.8 cutover: op_conditional + _evaluate_condition (helper
# privato, NON re-exported) moved to `transforms.filtering` (re-exported
# in testa al file via facade per la sola op_conditional).


# Phase 5 B1.3b cutover: op_text_normalize moved to `transforms.strings`
# (re-exported in testa al file via facade).


# Phase 5 B1.10 cutover: op_pivot moved to `transforms.reshape`
# (re-exported in testa al file via facade).


# Phase 5 B1.10 cutover: op_table_diff moved to transforms.reshape
# (re-exported in testa al file via facade).


# Phase 5 B1.9 cutover: op_sample moved to `transforms.analysis`
# (re-exported in testa al file via facade).


# Phase 5 B1.10 cutover: op_melt moved to transforms.reshape
# (re-exported in testa al file via facade).
# Phase 5 B1.3c cutover: op_add_row_number + op_date_extract moved to
# `transforms.utility` (re-exported in testa al file via facade).


# =============================================================================
# LOOKUP / BINNING / FLATTEN JSON / STATISTICS
# Phase 5 B1.9 cutover: cluster ANALYSIS (op_lookup, op_bin,
# op_flatten_json, op_statistics) moved to `transforms.analysis`
# (re-exported in testa al file via facade).
# =============================================================================


# Phase 5 B1.3b cutover: op_string_extract moved to `transforms.strings`
# (re-exported in testa al file via facade).


# Phase 5 B1.2 cutover: op_cross_join moved to `transforms.joins`
# (re-exported in testa al file via facade). Sezione lasciata vuota
# per preservare l'ancoraggio storico delle linee successive.


# Phase 5 B1.10 cutover: op_transpose moved to transforms.reshape
# (re-exported in testa al file via facade).
# Phase 5 B1.4 cutover: op_reorder_columns moved to `transforms.columns`
# (re-exported in testa al file via facade).


# Phase 5 B1.3a + B1.3b cutover: cluster STRINGS spostato in
# `app.core.manipola.transforms.strings`. Re-export per preservare i
# path pubblici esistenti:
#   - `from app.core.manipola.transforms.base import op_string_pad`
#       (idem op_string_length, op_string_extract, op_text_normalize)
#   - `from app.core.manipola.transforms import op_string_extract`
#     (idem op_text_normalize) — `__init__.py` resta `from .base import
#     op_string_extract, op_text_normalize` e cattura il facade.
#   - `transforms_registry.json` entries "base.op_string_*" /
#     "base.op_text_normalize" risolte via `getattr(<base>, "<func>")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .strings import (  # noqa: F401  (re-export)
    op_string_pad,
    op_string_length,
    op_string_extract,
    op_text_normalize,
)

# Phase 5 B1.3c cutover: cluster UTILITY (op_add_row_number,
# op_date_extract, op_uuid_generator) spostato in
# `app.core.manipola.transforms.utility`. Re-export per preservare i
# path pubblici esistenti:
#   - `from app.core.manipola.transforms.base import <op>`
#   - `transforms_registry.json` entries "base.op_add_row_number" /
#     "base.op_date_extract" / "base.op_uuid_generator" risolte via
#     `getattr(<base module>, "<func>")`.
# Nessuna delle 3 e' esposta da `transforms/__init__.py` (verificato
# pre-extraction) → nessun cambio init richiesto.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .utility import (  # noqa: F401  (re-export)
    op_add_row_number,
    op_date_extract,
    op_uuid_generator,
)

# Phase 5 B1.4 cutover: cluster COLUMNS (op_drop_columns, op_rename,
# op_reorder_columns, op_concat_columns, op_split_column) spostato in
# `app.core.manipola.transforms.columns`. Approvazione esplicita
# all'eccezione del target commit-size <600 (effettivo ~645, +45):
# cluster cohesive, 5 funzioni self-contained, zero coupling, rollback
# atomico. Re-export per preservare i path pubblici esistenti:
#   - `from app.core.manipola.transforms.base import <op>`
#   - `from app.core.manipola.transforms import <op>` per le 4 esposte
#     da __init__.py (op_concat_columns, op_rename, op_drop_columns,
#     op_split_column) — `__init__.py` resta `from .base import ...`
#     e cattura il facade. `op_reorder_columns` non e' in init.
#   - `transforms_registry.json` entries "base.<op>" risolte via
#     `getattr(<base module>, "<func>")` dal `PluginExecutor`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .columns import (  # noqa: F401  (re-export)
    op_drop_columns,
    op_rename,
    op_reorder_columns,
    op_concat_columns,
    op_split_column,
)

# Phase 5 B1.5 cutover: cluster FORMULA spostato in
# `app.core.manipola.transforms.formula`. Re-export di entrambi i nomi
# pubblici esistenti — il prefix-underscore `_validate_formula_ast` e'
# **intenzionalmente** re-exported perche' il test contract N4
# (`tests2/security/unit/test_n4_formula_validator_contract.py`) lo
# importa direttamente da `base` (3 occorrenze):
#   - `from app.core.manipola.transforms.base import op_formula`
#   - `from app.core.manipola.transforms.base import _validate_formula_ast`
#   - `from app.core.manipola.transforms import op_formula` (via __init__)
#   - `transforms_registry.json` entry "base.op_formula" risolta via
#     `getattr(<base module>, "op_formula")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .formula import (  # noqa: F401  (re-export)
    op_formula,
    _validate_formula_ast,
)

# Phase 5 B1.6 cutover: cluster CLEANSING (op_fill_na + op_replace +
# op_type_cast) spostato in `app.core.manipola.transforms.cleansing`.
# I 2 helper privati `_is_strict_numeric_series` e
# `_coerce_fill_value_for_numeric` restano single-source nel nuovo
# modulo (verificato pre-extraction: zero consumer esterni). Re-export
# delle 3 funzioni pubbliche per preservare i path:
#   - `from app.core.manipola.transforms.base import op_fill_na`
#       (idem op_replace, op_type_cast)
#   - `from app.core.manipola.transforms import op_fill_na` /
#     `op_replace` / `op_type_cast` — `__init__.py` resta `from .base
#     import ...` e cattura il facade.
#   - `transforms_registry.json` entries "base.op_fill_na" /
#     "base.op_replace" / "base.op_type_cast" risolte via
#     `getattr(<base module>, "<func>")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .cleansing import (  # noqa: F401  (re-export)
    op_fill_na,
    op_replace,
    op_type_cast,
)

# Phase 5 B1.7 cutover: cluster AGGREGATION (op_aggregate, op_sort,
# op_distinct, op_dedup_advanced, op_window_function) spostato in
# `app.core.manipola.transforms.aggregation`. Re-export per preservare
# i path pubblici esistenti:
#   - `from app.core.manipola.transforms.base import <op>` per tutte e 5
#   - `from app.core.manipola.transforms import op_aggregate` /
#     `op_sort` / `op_distinct` (via __init__) — `__init__.py` resta
#     `from .base import ...` e cattura il facade. `op_dedup_advanced`
#     e `op_window_function` non sono in init (verificato pre-extraction).
#   - `transforms_registry.json` entries "base.op_*" risolte via
#     `getattr(<base module>, "<func>")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .aggregation import (  # noqa: F401  (re-export)
    op_aggregate,
    op_sort,
    op_distinct,
    op_dedup_advanced,
    op_window_function,
)

# Phase 5 B1.8 cutover: cluster FILTERING (op_filter, op_conditional)
# spostato in `app.core.manipola.transforms.filtering`. Il private
# helper `_evaluate_condition` resta single-source nel nuovo modulo
# (verificato pre-extraction: zero consumer esterni, usato solo da
# op_conditional). Re-export delle 2 funzioni pubbliche per preservare
# i path:
#   - `from app.core.manipola.transforms.base import op_filter` /
#     `op_conditional`
#   - `from app.core.manipola.transforms import op_filter` /
#     `op_conditional` — `__init__.py` resta `from .base import ...`
#     e cattura il facade.
#   - `transforms_registry.json` entries "base.op_filter" /
#     "base.op_conditional" risolte via `getattr(<base>, "<func>")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .filtering import (  # noqa: F401  (re-export)
    op_filter,
    op_conditional,
)

# Phase 5 B1.9 cutover: cluster ANALYSIS (op_sample, op_lookup, op_bin,
# op_flatten_json, op_statistics) spostato in
# `app.core.manipola.transforms.analysis`. Re-export delle 5 funzioni
# pubbliche per preservare i path:
#   - `from app.core.manipola.transforms.base import <op>`
#   - `from app.core.manipola.transforms import op_statistics` —
#     unica esposta da `__init__.py` (le altre 4 non in init);
#     resta `from .base import op_statistics` e cattura il facade.
#   - `transforms_registry.json` entries "base.op_*" risolte via
#     `getattr(<base module>, "<func>")`.
# Cleanup naturale: `import json` rimosso da base.py (era usato solo
# da op_flatten_json, ora in analysis.py).
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .analysis import (  # noqa: F401  (re-export)
    op_sample,
    op_lookup,
    op_bin,
    op_flatten_json,
    op_statistics,
)

# Phase 5 B1.10 cutover: cluster RESHAPE (op_pivot, op_table_diff,
# op_melt, op_transpose) spostato in
# `app.core.manipola.transforms.reshape`. Re-export per preservare i
# path pubblici esistenti:
#   - `from app.core.manipola.transforms.base import <op>` per tutte e 4
#   - `from app.core.manipola.transforms import op_pivot` /
#     `op_table_diff` (le 2 esposte da __init__.py) — `__init__.py`
#     resta `from .base import ...` e cattura il facade. `op_melt` e
#     `op_transpose` non sono in init.
#   - `transforms_registry.json` entries "base.op_*" risolte via
#     `getattr(<base module>, "<func>")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .reshape import (  # noqa: F401  (re-export)
    op_pivot,
    op_table_diff,
    op_melt,
    op_transpose,
)


# Phase 5 B1.7 cutover: op_dedup_advanced + op_window_function moved to
# `transforms.aggregation` (re-exported in testa al file via facade).


# Phase 5 B1.3c cutover: op_uuid_generator moved to `transforms.utility`
# (re-exported in testa al file via facade).
