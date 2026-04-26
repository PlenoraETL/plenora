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

import json
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


# =============================================================================
# FILTRO RIGHE
# Applica condizioni su colonne con operatori: =, !=, >, <, >=, <=, LIKE, IN, IS NULL.
# =============================================================================

def op_filter(
    df: DataFrame,
    column: str,
    operator: str,
    value: Any = None
) -> DataFrame:
    """
    Filtra righe in base a una condizione.
    
    Args:
        df: DataFrame input
        column: Colonna da filtrare
        operator: Operatore (==, !=, >, >=, <, <=, contains, startswith, endswith, isnull, notnull)
        value: Valore da confrontare
        
    Returns:
        DataFrame filtrato
    """
    if column not in df.columns:
        # Fail-fast: un filtro che non filtra e' un bug grave. Silent return
        # df invariato nascondeva l'errore e i conteggi a valle erano sballati.
        available = list(df.columns)
        raise ValueError(
            f"Filter: colonna '{column}' non trovata. "
            f"Colonne disponibili: {available}"
        )

    result = df.copy()
    col = result[column]
    
    # === AUTO-CONVERSIONE TIPO ===
    # Se la colonna è numerica e il valore è stringa, converti
    if value is not None and pd.api.types.is_numeric_dtype(col):
        try:
            # Prova int prima, poi float
            if '.' in str(value):
                value = float(value)
            else:
                value = float(value)  # anche int passa per float per gestire NaN
        except (ValueError, TypeError):
            logger.warning(f"Filter: impossibile convertire '{value}' a numero per colonna {column}")
    
    try:
        if operator == '==':
            mask = col == value
        elif operator == '!=':
            mask = col != value
        elif operator == '>':
            # FIX B-BE-MANIPOLA-04: value è già convertito dall'auto-conversione sopra
            # Se non convertibile, value rimane stringa → confronto fallisce → df invariato
            mask = col > value
        elif operator == '>=':
            mask = col >= value
        elif operator == '<':
            mask = col < value
        elif operator == '<=':
            mask = col <= value
        elif operator == 'contains':
            # SECURITY: regex=False tratta il valore come stringa letterale.
            # Prima: regex=True (default) → un utente poteva iniettare pattern
            # catastrofici come ((a+)+)+b causando ReDoS (CPU hang sul worker).
            mask = col.astype(str).str.contains(str(value), case=False, na=False, regex=False)
        elif operator == 'startswith':
            mask = col.astype(str).str.startswith(str(value), na=False)
        elif operator == 'endswith':
            mask = col.astype(str).str.endswith(str(value), na=False)
        elif operator == 'isnull':
            mask = col.isnull()
        elif operator == 'notnull':
            mask = col.notnull()
        else:
            logger.warning(f"Operatore '{operator}' non supportato")
            return df
        
        result = result[mask].reset_index(drop=True)
        logger.info(f"Filter: {len(df)} -> {len(result)} righe")
        return result
        
    # intent: fail-closed — input utente invalido (operatore/valore
    # incompatibile con tipo colonna, runtime error pandas) solleva
    # ValueError con messaggio safe. str(e) tecnico preservato solo
    # nel logger.warning server-side per postmortem admin.
    except Exception as e:
        logger.warning(f"[GESTITO] Errore Filter '{operator}' su colonna '{column}': {e}")
        raise ValueError(
            f"Filter: operatore '{operator}' non applicabile sulla colonna "
            f"'{column}' con il valore fornito. Verificare tipo e sintassi."
        ) from e


# Phase 5 B1.5 cutover: cluster FORMULA (op_formula + _validate_formula_ast
# + AST whitelists + `import ast as _ast` locale) spostato in
# `app.core.manipola.transforms.formula`. Re-export in testa al file via
# facade. `import ast as _ast` rimosso da base.py: era usato esclusivamente
# da questo cluster, no consumer altrove.


# Phase 5 B1.4 cutover: op_concat_columns moved to `transforms.columns`
# (re-exported in testa al file via facade).


# =============================================================================
# AGGREGAZIONE (GROUP BY)
# Raggruppa per colonne e applica funzioni: count, sum, mean, min, max, first, last.
# =============================================================================

def op_aggregate(
    df: DataFrame,
    group_by: List[str],
    aggregations: List[Dict[str, str]] = None
) -> DataFrame:
    """
    Raggruppa dati e applica funzioni di aggregazione.
    
    Args:
        df: DataFrame input
        group_by: Lista colonne per raggruppamento
        aggregations: Lista di {'column': 'col', 'function': 'sum|mean|count|min|max|concat', ...}
                      Per concat: {'column': 'col', 'function': 'concat', 'separator': ', ', 'distinct': false, 'skip_null': true}
        
    Returns:
        DataFrame aggregato
    """
    logger.info(f"op_aggregate CALLED - group_by: {group_by}, aggregations: {aggregations}, type(aggregations): {type(aggregations).__name__}")
    
    if not group_by:
        logger.warning("Aggregate: nessuna colonna group_by")
        return df
    
    # Filtra colonne esistenti
    group_by = [c for c in group_by if c in df.columns]
    if not group_by:
        logger.warning("Aggregate: nessuna colonna group_by valida")
        return df
    
    try:
        # Se nessuna aggregazione, solo count.
        # dropna=False: includiamo i gruppi con NaN nelle chiavi per non
        # perdere righe silenziosamente (default pandas e' dropna=True che
        # le scarta — comportamento sorprendente per chi aggrega).
        if not aggregations:
            result = df.groupby(group_by, dropna=False).size().reset_index(name='count')
            logger.info(f"Aggregate (count): {len(df)} -> {len(result)} righe")
            return result
        
        # Funzione concat personalizzata
        def make_concat_agg(separator=", ", distinct=False, skip_null=True):
            def concat_agg(values):
                vals = list(values)
                if skip_null:
                    vals = [v for v in vals if v is not None and pd.notna(v)]
                if distinct:
                    vals = list(dict.fromkeys(vals))  # preserva ordine
                return separator.join(str(v) for v in vals)
            return concat_agg
        
        # Costruisci dizionario aggregazioni
        agg_dict = {}
        func_map = {
            'count': 'count',
            'sum': 'sum',
            'avg': 'mean',
            'mean': 'mean',
            'min': 'min',
            'max': 'max',
            'first': 'first',
            'last': 'last'
        }
        
        for agg in aggregations:
            col = agg.get('column')
            func = agg.get('function', 'count').lower()
            logger.info(f"  Aggregazione: col='{col}', func='{func}', col_in_df={col in df.columns if col else False}")
            
            if col not in df.columns:
                logger.warning(f"  Colonna '{col}' non trovata! Colonne disponibili: {list(df.columns)}")
                continue
            
            # Gestione speciale per concat
            if func == 'concat':
                separator = agg.get('separator', ', ')
                distinct = agg.get('distinct', False)
                skip_null = agg.get('skip_null', True)
                pandas_func = make_concat_agg(separator, distinct, skip_null)
            else:
                pandas_func = func_map.get(func, func)
            
            if col in agg_dict:
                if isinstance(agg_dict[col], list):
                    agg_dict[col].append(pandas_func)
                else:
                    agg_dict[col] = [agg_dict[col], pandas_func]
            else:
                agg_dict[col] = pandas_func
        
        if not agg_dict:
            logger.warning(f"agg_dict vuoto dopo parsing aggregazioni! Fallback a count")
            result = df.groupby(group_by, dropna=False).size().reset_index(name='count')
        else:
            logger.info(f"agg_dict finale: {agg_dict}")
            result = df.groupby(group_by, dropna=False).agg(agg_dict).reset_index()
            # Flatten multi-index columns
            if isinstance(result.columns, pd.MultiIndex):
                result.columns = ['_'.join(col).strip('_') for col in result.columns]
        
        logger.info(f"Aggregate: {len(df)} -> {len(result)} righe")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Aggregate: {e}", exc_info=True)
        raise


# =============================================================================
# ORDINAMENTO
# Ordina DataFrame per una o più colonne (asc/desc).
# =============================================================================

def op_sort(
    df: DataFrame,
    columns: List[str],
    ascending: bool = True
) -> DataFrame:
    """
    Ordina le righe per una o più colonne.
    
    Args:
        df: DataFrame input
        columns: Colonne per ordinamento
        ascending: True per crescente, False per decrescente
        
    Returns:
        DataFrame ordinato
    """
    if not columns:
        return df
    
    # Filtra colonne esistenti
    columns = [c for c in columns if c in df.columns]
    if not columns:
        return df
    
    try:
        result = df.sort_values(by=columns, ascending=ascending).reset_index(drop=True)
        return result
    except Exception as e:
        logger.error(f"[CRASH] Errore Sort: {e}", exc_info=True)
        raise


# =============================================================================
# DEDUPLICAZIONE
# Rimuove righe duplicate su colonne selezionate. Default: tutte le colonne.
# =============================================================================

def op_distinct(
    df: DataFrame,
    subset: List[str] = None,
    keep: str = "first"
) -> DataFrame:
    """
    Rimuove righe duplicate.
    
    Args:
        df: DataFrame input
        subset: Colonne da considerare (None = tutte)
        keep: 'first', 'last', o 'false' (rimuovi tutti i duplicati)
        
    Returns:
        DataFrame senza duplicati
    """
    try:
        if subset:
            subset = [c for c in subset if c in df.columns] or None
        
        keep_val = False if keep == 'false' else keep
        
        result = df.drop_duplicates(subset=subset, keep=keep_val).reset_index(drop=True)
        logger.info(f"Distinct: {len(df)} -> {len(result)} righe")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Distinct: {e}", exc_info=True)
        raise


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


# =============================================================================
# CONDIZIONALE (CASE WHEN)
# Crea colonna con valore dipendente da condizioni (simile a SQL CASE WHEN).
# =============================================================================

def op_conditional(
    df: DataFrame,
    column: str,
    conditions: List[Dict[str, Any]],
    default_value: str = "",
    output_column: str = "result"
) -> DataFrame:
    """
    Crea colonna con logica case/when su condizioni multiple.
    
    Args:
        df: DataFrame input
        column: Colonna da valutare
        conditions: Lista di {operator, value, result}
            operator: ==, !=, >, >=, <, <=, contains, startswith, endswith, isnull, notnull, between
            value: Valore di confronto (o "min,max" per between)
            result: Valore da assegnare se la condizione è vera
        default_value: Valore se nessuna condizione è soddisfatta
        output_column: Nome colonna output
        
    Returns:
        DataFrame con nuova colonna
    """
    if not column or column not in df.columns:
        logger.warning(f"Conditional: colonna '{column}' non trovata")
        return df
    
    if not conditions:
        logger.warning("Conditional: nessuna condizione definita")
        return df
    
    result = df.copy()
    
    try:
        # Log diagnostico: valori unici della colonna sorgente
        unique_vals = result[column].dropna().unique()
        logger.info(f"Conditional: colonna '{column}' - {len(unique_vals)} valori unici, sample: {list(unique_vals[:10])}, dtype: {result[column].dtype}")
        
        # Inizializza con default
        result[output_column] = default_value
        
        col_values = result[column]
        
        # Applica condizioni in ordine inverso (la prima ha priorità)
        total_matched = 0
        for cond in reversed(conditions):
            op = cond.get('operator', '==')
            val = cond.get('value', '')
            res = cond.get('result', '')
            
            try:
                mask = _evaluate_condition(col_values, op, val)
                match_count = mask.sum()
                total_matched += match_count
                result.loc[mask, output_column] = res
                logger.info(f"Conditional:   {op} '{val}' -> '{res}' : {match_count} righe matchate")
            except Exception as e:
                logger.warning(f"Conditional: errore condizione {op} {val}: {e}")
                continue
        
        logger.info(f"Conditional: totale matchate={total_matched}, non matchate={len(result) - total_matched} (default='{default_value}')")
        
        # Auto-detect tipo numerico: se tutti i result values (e default) 
        # sono numerici, converti la colonna a numerico
        all_values = [cond.get('result', '') for cond in conditions] + [default_value]
        all_numeric = True
        for v in all_values:
            if v is None or str(v).strip() == '':
                continue  # vuoto/None ok, diventa NaN
            try:
                float(str(v).replace(',', '.'))
            except (ValueError, TypeError):
                all_numeric = False
                break
        
        if all_numeric:
            result[output_column] = pd.to_numeric(result[output_column], errors='coerce')
            logger.info(f"Conditional: '{output_column}' convertita a numerico (dtype: {result[output_column].dtype})")
        
        logger.info(f"Conditional: {column} -> {output_column} ({len(conditions)} condizioni)")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Conditional: {e}", exc_info=True)
        raise


def _evaluate_condition(col_values, operator: str, value: str):
    """Valuta una condizione e restituisce una maschera booleana."""
    
    if operator == 'isnull':
        return col_values.isna()
    elif operator == 'notnull':
        return col_values.notna()
    
    # Prova conversione numerica del valore
    numeric_val = None
    try:
        numeric_val = float(value)
    except (ValueError, TypeError):
        pass
    
    if operator == '==':
        if numeric_val is not None:
            return pd.to_numeric(col_values, errors='coerce') == numeric_val
        return col_values.astype(str) == str(value)
    
    elif operator == '!=':
        if numeric_val is not None:
            return pd.to_numeric(col_values, errors='coerce') != numeric_val
        return col_values.astype(str) != str(value)
    
    elif operator in ('>', '>=', '<', '<='):
        num_col = pd.to_numeric(col_values, errors='coerce')
        if numeric_val is None:
            numeric_val = 0
        if operator == '>':
            return num_col > numeric_val
        elif operator == '>=':
            return num_col >= numeric_val
        elif operator == '<':
            return num_col < numeric_val
        elif operator == '<=':
            return num_col <= numeric_val
    
    elif operator == 'contains':
        # SECURITY: regex=False (anti-ReDoS). Vedi commento in op_filter.
        return col_values.astype(str).str.contains(str(value), case=False, na=False, regex=False)
    
    elif operator == 'startswith':
        return col_values.astype(str).str.startswith(str(value), na=False)
    
    elif operator == 'endswith':
        return col_values.astype(str).str.endswith(str(value), na=False)
    
    elif operator == 'between':
        parts = str(value).split(',')
        if len(parts) == 2:
            low = float(parts[0].strip())
            high = float(parts[1].strip())
            num_col = pd.to_numeric(col_values, errors='coerce')
            return (num_col >= low) & (num_col <= high)
    
    return pd.Series(False, index=col_values.index)


# Phase 5 B1.3b cutover: op_text_normalize moved to `transforms.strings`
# (re-exported in testa al file via facade).


# =============================================================================
# PIVOT TABLE
# Trasforma righe in colonne: la colonna pivot_col viene "spalmata"
# orizzontalmente, i valori di value_col diventano i contenuti,
# e index_col diventa la chiave primaria del risultato.
#
# Esempio classico ICSC:
#   immobile | tipo_valore | importo       immobile | canone | rendita
#   ---------------------+--------  →    -----------+--------+--------
#   A        | canone      | 1000         A        | 1000   | 500
#   A        | rendita     | 500          B        | 2000   | NULL
#   B        | canone      | 2000
# =============================================================================

def op_pivot(
    df: DataFrame,
    index_col: str,
    pivot_col: str,
    value_col: str,
    aggr_func: str = "first",
    mapping: Optional[Dict[str, str]] = None
) -> DataFrame:
    """
    Tabella pivot: trasforma righe in colonne.
    
    Prende i valori distinti di `pivot_col`, li "spalma" come colonne,
    e riempie ciascuna con il valore corrispondente di `value_col`,
    aggregato tramite `aggr_func`.
    
    Args:
        df: DataFrame input
        index_col: Colonna chiave (diventa la PK del risultato).
                   Accetta anche più colonne separate da virgola.
        pivot_col: Colonna che contiene i "tipi" da spalmare come colonne
        value_col: Colonna con i valori da estrarre
        aggr_func: Funzione di aggregazione da applicare quando ci sono
                   duplicati (index_col + pivot_col non univoci).
                   Valori: first | last | max | min | sum | mean | count | concat
        mapping: Dizionario opzionale { valore_pivot_col → nome_colonna_output }.
                 Se fornito, vengono create SOLO le colonne nel mapping;
                 valori di pivot_col non nel mapping vengono scartati.
                 Se vuoto/None, una colonna per ogni valore unico di pivot_col.
        
    Returns:
        DataFrame "pivotato" con index_col come colonna(e) e una colonna
        per ogni valore (o mapping) di pivot_col.
        
    Example:
        # Input:
        #   immobile | tipo    | importo
        #   A        | canone  | 1000
        #   A        | rendita | 500
        #   B        | canone  | 2000
        
        result = op_pivot(
            df,
            index_col="immobile",
            pivot_col="tipo",
            value_col="importo",
            aggr_func="max",
            mapping={"canone": "val_canone", "rendita": "val_rendita"}
        )
        # Output:
        #   immobile | val_canone | val_rendita
        #   A        | 1000       | 500
        #   B        | 2000       | NaN
    """
    # --- Validazioni --------------------------------------------------------
    if not index_col or not pivot_col or not value_col:
        logger.warning("Pivot: parametri obbligatori mancanti (index_col, pivot_col, value_col)")
        return df
    
    # Supporto multi-index: "col1, col2" → ["col1", "col2"]
    if isinstance(index_col, str) and ',' in index_col:
        index_cols = [c.strip() for c in index_col.split(',') if c.strip()]
    elif isinstance(index_col, list):
        index_cols = index_col
    else:
        index_cols = [index_col]
    
    # Verifica esistenza colonne
    all_needed = index_cols + [pivot_col, value_col]
    missing = [c for c in all_needed if c not in df.columns]
    if missing:
        logger.warning(f"[GESTITO] Pivot: colonne mancanti: {missing}")
        return df
    
    result = df.copy()
    result[pivot_col] = result[pivot_col].fillna('').astype(str).str.strip()
    try:
        # --- Filtra solo valori nel mapping (se fornito) --------------------
        if mapping:
            allowed_values = list(mapping.keys())
            result = result[result[pivot_col].isin(allowed_values)]
            if result.empty:
                logger.warning(
                    f"Pivot: nessun valore di '{pivot_col}' corrisponde "
                    f"al mapping {allowed_values}"
                )
                return df
        
        # --- Funzione di aggregazione --------------------------------------
        aggr_func_lower = aggr_func.lower().strip() if aggr_func else 'first'
        
        if aggr_func_lower == 'concat':
            # Aggregazione speciale: concatena come stringa separata da virgola
            agg_fn = lambda x: ', '.join(x.dropna().astype(str))
        elif aggr_func_lower in ('first', 'last', 'max', 'min', 'sum', 'mean', 'count'):
            agg_fn = aggr_func_lower
        else:
            logger.warning(f"Pivot: funzione '{aggr_func}' non supportata, uso 'first'")
            agg_fn = 'first'
        
        # --- Costruzione pivot table ---------------------------------------
        pivoted = pd.pivot_table(
            result,
            index=index_cols,
            columns=pivot_col,
            values=value_col,
            aggfunc=agg_fn
        )
        
        # Flatten MultiIndex colonne
        if isinstance(pivoted.columns, pd.MultiIndex):
            pivoted.columns = [
                '_'.join(str(c) for c in col).strip('_')
                for col in pivoted.columns
            ]
        else:
            pivoted.columns = [str(c) for c in pivoted.columns]
        
        # Rimuovi nome asse colonne (metadata pandas)
        pivoted.columns.name = None
        
        # Riporta index_cols come colonne normali
        pivoted = pivoted.reset_index()
        
        # --- Applica mapping nomi colonne output ---------------------------
        if mapping:
            rename_map = {}
            for pivot_val, output_name in mapping.items():
                pivot_val_str = str(pivot_val)
                if pivot_val_str in pivoted.columns:
                    rename_map[pivot_val_str] = output_name
                else:
                    logger.warning(
                        f"Pivot: valore '{pivot_val}' non trovato "
                        f"nei risultati pivot"
                    )
            
            if rename_map:
                pivoted = pivoted.rename(columns=rename_map)
            
            # Tieni solo le colonne index + quelle mappate
            keep_cols = index_cols + list(rename_map.values())
            keep_cols = [c for c in keep_cols if c in pivoted.columns]
            pivoted = pivoted[keep_cols]
        
        logger.info(
            f"Pivot: {len(df)} righe -> {len(pivoted)} righe x "
            f"{len(pivoted.columns)} colonne "
            f"(index={index_cols}, pivot={pivot_col}, "
            f"value={value_col}, aggr={aggr_func_lower})"
        )
        return pivoted
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Pivot: {e}", exc_info=True)
        raise


# ============================================================================
# TABLE DIFF (Confronta Tabelle)
# ============================================================================

def op_table_diff(
    inputs: List[DataFrame],
    left_keys: Union[str, List[str]] = "",
    right_keys: Union[str, List[str]] = "",
    compare_columns: Union[str, List[str], None] = None,
    include_unchanged: str = "no",
    separator: str = "#"
) -> DataFrame:
    """
    Confronta due tabelle e identifica righe aggiunte, eliminate e modificate.
    
    Args:
        inputs: Lista di 2 DataFrame [vecchia (SX), nuova (DX)]
        left_keys: Colonne chiave tabella vecchia (SX)
        right_keys: Colonne chiave tabella nuova (DX)
        compare_columns: Colonne da confrontare (vuoto = tutte non-chiave)
        include_unchanged: "yes" per includere righe invariate
        separator: Separatore per _diff_columns e _diff_old_values (default: #)
        
    Returns:
        DataFrame con colonne:
        - _diff_status: ADDED, DELETED, MODIFIED, UNCHANGED
        - _diff_columns: colonne modificate (separate da separator)
        - _diff_old_values: valori precedenti (stesso ordine, stesso separator)
    """
    if len(inputs) < 2:
        logger.warning("Table Diff richiede 2 input")
        return inputs[0] if inputs else pd.DataFrame()
    
    old_df = inputs[0].copy()
    new_df = inputs[1].copy()
    sep = separator.strip() if separator else "#"
    
    # === Normalizza chiavi ===
    if isinstance(left_keys, str):
        left_keys = [k.strip() for k in left_keys.split(",") if k.strip()]
    if isinstance(right_keys, str):
        right_keys = [k.strip() for k in right_keys.split(",") if k.strip()]
    
    if not left_keys or not right_keys:
        logger.warning("[GESTITO] Table Diff: specificare le colonne chiave per entrambe le tabelle")
        return old_df
    
    if len(left_keys) != len(right_keys):
        logger.error(
            f"Table Diff: numero chiavi diverso "
            f"(SX: {len(left_keys)}, DX: {len(right_keys)})"
        )
        return old_df
    
    # Verifica che le chiavi esistano
    for k in left_keys:
        if k not in old_df.columns:
            logger.warning(f"Table Diff: chiave '{k}' non trovata in tabella SX")
            return old_df
    for k in right_keys:
        if k not in new_df.columns:
            logger.warning(f"Table Diff: chiave '{k}' non trovata in tabella DX")
            return old_df
    
    # === Normalizza colonne confronto ===
    if isinstance(compare_columns, str):
        compare_columns = [c.strip() for c in compare_columns.split(",") if c.strip()]
    if not compare_columns:
        compare_columns = None  # confronta tutte
    
    # === Rinomina chiavi DX per allinearle a SX se diverse ===
    key_rename = {}
    for lk, rk in zip(left_keys, right_keys):
        if lk != rk:
            key_rename[rk] = lk
    if key_rename:
        new_df = new_df.rename(columns=key_rename)
    
    # Le chiavi unificate (nomi SX)
    key_cols = list(left_keys)
    
    # === Auto-conversione tipi chiave ===
    for k in key_cols:
        if old_df[k].dtype != new_df[k].dtype:
            old_df[k] = old_df[k].astype(str)
            new_df[k] = new_df[k].astype(str)
    
    # === Colonne non-chiave da confrontare ===
    old_value_cols = [c for c in old_df.columns if c not in key_cols]
    new_value_cols = [c for c in new_df.columns if c not in key_cols]
    common_value_cols = [c for c in old_value_cols if c in new_value_cols]
    
    if compare_columns:
        common_value_cols = [c for c in compare_columns if c in common_value_cols]
    
    # Colonne presenti solo in una tabella
    only_old_cols = [c for c in old_value_cols if c not in new_value_cols]
    only_new_cols = [c for c in new_value_cols if c not in old_value_cols]
    
    # === Merge outer ===
    merged = old_df.merge(
        new_df,
        on=key_cols,
        how='outer',
        suffixes=('_OLD', '_NEW'),
        indicator=True
    )
    
    # === Classificazione righe — vectorized ===
    # Prima: iterrows su ogni riga con confronto cella-per-cella Python.
    # Su 1M righe -> timeout garantito.
    # Ora: boolean masks pandas + 1 pass per raccogliere le colonne cambiate
    # (costretto ad iterare, ma su un DataFrame piccolo di boolean).

    merge_type = merged['_merge']
    mask_deleted = merge_type == 'left_only'
    mask_added = merge_type == 'right_only'
    mask_both = merge_type == 'both'

    # Per ogni common_value_col, calcola maschera "e' cambiato" vectorized.
    # Regole (preservate dal comportamento precedente):
    #   - entrambi NaN -> uguali (non changed)
    #   - uno NaN e l'altro no -> changed
    #   - entrambi numerici -> abs(diff) > 1e-9
    #   - altrimenti -> str(old) != str(new)
    changed_masks = {}
    for c in common_value_cols:
        old_s = merged[f'{c}_OLD']
        new_s = merged[f'{c}_NEW']
        both_nan = old_s.isna() & new_s.isna()
        one_nan = old_s.isna() ^ new_s.isna()
        if pd.api.types.is_numeric_dtype(old_s) and pd.api.types.is_numeric_dtype(new_s):
            # Confronto numerico con tolleranza 1e-9
            numeric_diff = (old_s.fillna(0) - new_s.fillna(0)).abs() > 1e-9
            # Se uno solo e' NaN -> changed (one_nan), altrimenti solo se diff > tol
            col_changed = one_nan | (numeric_diff & ~both_nan)
        else:
            # Confronto stringa (fallback generico)
            str_diff = old_s.astype(str) != new_s.astype(str)
            col_changed = one_nan | (str_diff & ~both_nan & ~one_nan)
        changed_masks[c] = col_changed

    changed_df = pd.DataFrame(changed_masks, index=merged.index) if common_value_cols else pd.DataFrame(index=merged.index)

    # Status per riga
    any_changed = changed_df.any(axis=1) if len(changed_df.columns) else pd.Series(False, index=merged.index)
    status = pd.Series('UNCHANGED', index=merged.index, dtype=object)
    status[mask_deleted] = 'DELETED'
    status[mask_added] = 'ADDED'
    status[mask_both & any_changed] = 'MODIFIED'

    # Filtra UNCHANGED se non richiesti
    if include_unchanged != "yes":
        keep = status != 'UNCHANGED'
        merged = merged[keep]
        changed_df = changed_df.loc[keep] if len(changed_df.columns) else changed_df
        status = status[keep]

    if len(merged) == 0:
        logger.info("Table Diff: nessuna differenza trovata")
        return pd.DataFrame(
            columns=key_cols + common_value_cols + ['_diff_status', '_diff_columns', '_diff_old_values']
        )

    # Costruzione result_df vectorized
    result_df = pd.DataFrame(index=merged.index)
    for k in key_cols:
        result_df[k] = merged[k]

    # Per ADDED e MODIFIED prendi _NEW; per DELETED prendi _OLD.
    # Uso np.where per selezione vectorized.
    import numpy as np
    is_deleted = (status == 'DELETED').values
    for c in common_value_cols:
        old_col = f'{c}_OLD'
        new_col = f'{c}_NEW'
        result_df[c] = np.where(is_deleted, merged[old_col], merged[new_col])
    for c in only_old_cols:
        if c in merged.columns:
            result_df[c] = merged[c]
    for c in only_new_cols:
        if c in merged.columns:
            result_df[c] = merged[c]

    # _diff_columns e _diff_old_values solo per MODIFIED — vectorized
    # via stack+groupby.agg(join). Niente loop Python (pandas internals in C).
    diff_columns_col = pd.Series([None] * len(merged), index=merged.index, dtype=object)
    diff_old_values_col = pd.Series([None] * len(merged), index=merged.index, dtype=object)

    modified_mask = status == 'MODIFIED'
    if modified_mask.any() and common_value_cols:
        cd_mod = changed_df.loc[modified_mask]  # (n_mod, n_cols) bool

        # ── _diff_columns ──────────────────────────────────────────────
        # Stack: (row, col) -> bool. Filtra True, groupby row e join level 1.
        stacked = cd_mod.stack()
        trues = stacked[stacked]
        if len(trues) > 0:
            diff_cols_per_row = (
                trues.reset_index(level=1)
                .groupby(level=0)[trues.index.names[1] or 'level_1']
                .agg(sep.join)
                .reindex(cd_mod.index, fill_value='')
            )
            diff_columns_col.loc[modified_mask] = diff_cols_per_row.values

        # ── _diff_old_values ───────────────────────────────────────────
        # DataFrame parallelo di old values stringificati, stessa shape di cd_mod.
        old_df = pd.DataFrame(index=cd_mod.index)
        for c in common_value_cols:
            ov = merged.loc[modified_mask, f'{c}_OLD']
            # Vectorized: NaN -> '', altrimenti str()
            old_df[c] = ov.where(ov.notna(), '').astype(str)
            # Rimuovi le occorrenze di 'nan' che astype(str) su float puo' produrre
            # (solo dove ov era effettivamente NaN — gia' gestito da where).

        if len(trues) > 0:
            # Allinea old values con le posizioni True usando stesso MultiIndex
            old_stacked = old_df.stack()
            old_at_changes = old_stacked.loc[trues.index]
            diff_old_per_row = (
                old_at_changes.groupby(level=0)
                .agg(sep.join)
                .reindex(cd_mod.index, fill_value='')
            )
            diff_old_values_col.loc[modified_mask] = diff_old_per_row.values

    result_df['_diff_status'] = status.values
    result_df['_diff_columns'] = diff_columns_col.values
    result_df['_diff_old_values'] = diff_old_values_col.values
    # Ripulisci l'index (merged ha index ereditato, non serve al consumer)
    result_df = result_df.reset_index(drop=True)

    # Statistiche
    status_counts = result_df['_diff_status'].value_counts().to_dict()
    logger.info(
        f"Table Diff completato: {len(result_df)} righe totali — "
        f"ADDED: {status_counts.get('ADDED', 0)}, "
        f"DELETED: {status_counts.get('DELETED', 0)}, "
        f"MODIFIED: {status_counts.get('MODIFIED', 0)}, "
        f"UNCHANGED: {status_counts.get('UNCHANGED', 0)}"
    )

    return result_df


# =============================================================================
# CAMPIONAMENTO RIGHE
# Estrae un campione casuale di righe (per numero o per frazione).
# Supporta campionamento stratificato proporzionale.
# =============================================================================

def op_sample(
    df: DataFrame,
    n: int = 100,
    fraction: Optional[float] = None,
    random_state: Optional[int] = None,
    stratify_column: Optional[str] = None
) -> DataFrame:
    """
    Campionamento casuale di righe.

    Args:
        df: DataFrame input
        n: Numero di righe da campionare (ignorato se fraction è impostato)
        fraction: Frazione di righe (0.0-1.0), se impostato ha priorità su n
        random_state: Seed per riproducibilità
        stratify_column: Colonna per campionamento stratificato proporzionale

    Returns:
        DataFrame campionato
    """
    if len(df) == 0:
        return df

    try:
        result = df.copy()

        if stratify_column and stratify_column in result.columns:
            # Campionamento stratificato: proporzioni uguali da ogni gruppo
            groups = []
            for _, group in result.groupby(stratify_column):
                if fraction is not None:
                    group_n = max(1, int(len(group) * fraction))
                else:
                    # Proporzione del gruppo rispetto al totale
                    group_n = max(1, int(n * len(group) / max(len(result), 1)))

                group_n = min(group_n, len(group))
                groups.append(group.sample(n=group_n, random_state=random_state))

            result = pd.concat(groups, ignore_index=True)
        else:
            if fraction is not None:
                fraction = max(0.0, min(1.0, fraction))
                result = result.sample(frac=fraction, random_state=random_state).reset_index(drop=True)
            else:
                n = min(n, len(result))
                result = result.sample(n=n, random_state=random_state).reset_index(drop=True)

        logger.info(f"Sample: {len(df)} -> {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Sample: {e}", exc_info=True)
        raise


# =============================================================================
# MELT (UNPIVOT)
# Trasforma colonne in righe: da formato wide a formato long.
# =============================================================================

def op_melt(
    df: DataFrame,
    id_columns: List[str],
    value_columns: Optional[List[str]] = None,
    var_name: str = "variable",
    value_name: str = "value"
) -> DataFrame:
    """
    Unpivot: trasforma colonne in righe (wide → long).

    Args:
        df: DataFrame input
        id_columns: Colonne da mantenere fisse (identificatori)
        value_columns: Colonne da sciogliere in righe (None = tutte le altre)
        var_name: Nome della colonna che conterrà i nomi delle variabili
        value_name: Nome della colonna che conterrà i valori

    Returns:
        DataFrame in formato long
    """
    try:
        # Filtra colonne esistenti
        id_columns = [c for c in id_columns if c in df.columns]
        if value_columns:
            value_columns = [c for c in value_columns if c in df.columns]
            if not value_columns:
                value_columns = None

        result = pd.melt(
            df,
            id_vars=id_columns or None,
            value_vars=value_columns,
            var_name=var_name,
            value_name=value_name
        )

        logger.info(
            f"Melt: {len(df)} righe x {len(df.columns)} colonne -> "
            f"{len(result)} righe x {len(result.columns)} colonne"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Melt: {e}", exc_info=True)
        raise


# Phase 5 B1.3c cutover: op_add_row_number + op_date_extract moved to
# `transforms.utility` (re-exported in testa al file via facade).


# =============================================================================
# LOOKUP — Mappatura valori tramite dizionario
# Sostituisce valori di una colonna secondo una mappa chiave→valore.
# =============================================================================

def op_lookup(
    df: DataFrame,
    column: str,
    mapping: Dict[str, Any],
    default: Any = None,
    output_column: Optional[str] = None
) -> DataFrame:
    """
    Mappa valori di una colonna tramite dizionario.

    Args:
        df: DataFrame input
        column: Colonna da mappare
        mapping: Dizionario chiave→valore per la sostituzione
        default: Valore per chiavi non trovate (None = mantieni originale)
        output_column: Colonna di output (None = sovrascrive la colonna sorgente)

    Returns:
        DataFrame con valori mappati
    """
    if column not in df.columns:
        logger.warning(f"Lookup: colonna '{column}' non trovata")
        return df

    if not mapping:
        logger.warning("Lookup: mapping vuoto, nessuna modifica")
        return df

    try:
        result = df.copy()
        target = output_column or column

        mapped = result[column].map(mapping)

        if default is not None:
            # Dove la mappa non ha trovato corrispondenza, usa default
            result[target] = mapped.fillna(default)
        else:
            # Dove la mappa non ha trovato corrispondenza, mantieni originale
            result[target] = mapped.fillna(result[column])

        mapped_count = result[column].isin(mapping.keys()).sum()
        logger.info(f"Lookup: {mapped_count}/{len(result)} valori mappati nella colonna '{target}'")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Lookup: {e}", exc_info=True)
        raise


# =============================================================================
# DISCRETIZZAZIONE (BINNING)
# Suddivide una colonna numerica in intervalli (bin) discreti.
# =============================================================================

def op_bin(
    df: DataFrame,
    column: str,
    bins: Any = 5,
    labels: Optional[List[str]] = None,
    output_column: Optional[str] = None
) -> DataFrame:
    """
    Discretizza una colonna numerica in intervalli.

    Args:
        df: DataFrame input
        column: Colonna numerica da discretizzare
        bins: Numero di bin (int) o lista di bordi (es. [0, 10, 50, 100])
        labels: Etichette personalizzate per i bin (lunghezza = numero bin)
        output_column: Colonna di output (None = "{column}_bin")

    Returns:
        DataFrame con colonna discretizzata
    """
    if column not in df.columns:
        logger.warning(f"Bin: colonna '{column}' non trovata")
        return df

    try:
        result = df.copy()
        target = output_column or f"{column}_bin"

        # Converti a numerico se necessario
        numeric_col = pd.to_numeric(result[column], errors='coerce')

        result[target] = pd.cut(
            numeric_col,
            bins=bins,
            labels=labels,
            include_lowest=True
        )

        n_bins = len(labels) if labels else (len(bins) - 1 if isinstance(bins, list) else bins)
        logger.info(f"Bin: colonna '{column}' discretizzata in {n_bins} intervalli -> '{target}'")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Bin: {e}", exc_info=True)
        raise


# =============================================================================
# FLATTEN JSON
# Espande una colonna contenente JSON/dict in colonne separate.
# =============================================================================

def op_flatten_json(
    df: DataFrame,
    column: str,
    prefix: str = "",
    max_level: int = 1
) -> DataFrame:
    """
    Espande una colonna JSON/dict in colonne separate.

    Supporta sia colonne con dict Python sia colonne con stringhe JSON.

    Args:
        df: DataFrame input
        column: Colonna contenente JSON/dict
        prefix: Prefisso per le colonne generate
        max_level: Livello massimo di nidificazione da espandere

    Returns:
        DataFrame con colonne JSON espanse
    """
    if column not in df.columns:
        logger.warning(f"FlattenJSON: colonna '{column}' non trovata")
        return df

    try:
        result = df.copy()

        # Parse stringhe JSON in dict
        def parse_json(val):
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    return parsed if isinstance(parsed, dict) else {}
                except (json.JSONDecodeError, TypeError):
                    return {}
            return {}

        parsed_col = result[column].apply(parse_json)

        # Normalizza in DataFrame piatto
        flat = pd.json_normalize(parsed_col.tolist(), max_level=max_level)

        if flat.empty:
            logger.warning(f"FlattenJSON: nessun dato JSON valido in '{column}'")
            return df

        # Applica prefisso alle colonne
        col_prefix = prefix if prefix else f"{column}_"
        flat.columns = [f"{col_prefix}{c}" for c in flat.columns]

        # Allinea indici e unisci
        flat.index = result.index
        result = pd.concat([result, flat], axis=1)

        logger.info(f"FlattenJSON: {len(flat.columns)} colonne estratte da '{column}'")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore FlattenJSON: {e}", exc_info=True)
        raise


# =============================================================================
# STATISTICS — StatisticsCalculator
# Calcola statistiche descrittive per colonna, con raggruppamento opzionale.
# =============================================================================

def op_statistics(
    df: DataFrame,
    column: str,
    group_by: str = None,
    stats: List[str] = None,
    output_prefix: str = ""
) -> DataFrame:
    """
    Calcola statistiche descrittive su una colonna numerica,
    opzionalmente raggruppate per un'altra colonna.

    Equivalente FME: StatisticsCalculator.

    Le statistiche vengono aggiunte come nuove colonne al DataFrame
    originale. Se group_by è specificato, ogni riga ottiene le
    statistiche del proprio gruppo.

    Args:
        df: DataFrame input
        column: Colonna numerica su cui calcolare le statistiche
        group_by: Colonna per raggruppamento (opzionale)
        stats: Lista di statistiche da calcolare. Default:
               ["count", "min", "max", "mean", "median", "std"].
               Valori supportati: count, min, max, sum, mean, median,
               std, var, q25, q75
        output_prefix: Prefisso per le colonne output.
                       Se vuoto, usa "{column}_"

    Returns:
        DataFrame con colonne statistiche aggiunte
    """
    if column not in df.columns:
        logger.warning(f"Statistics: colonna '{column}' non trovata")
        return df

    if stats is None:
        stats = ["count", "min", "max", "mean", "median", "std"]

    # Mappa nomi stat -> funzioni pandas
    stat_funcs = {
        "count": "count",
        "min": "min",
        "max": "max",
        "sum": "sum",
        "mean": "mean",
        "median": "median",
        "std": "std",
        "var": "var",
        "q25": lambda x: x.quantile(0.25),
        "q75": lambda x: x.quantile(0.75),
    }

    prefix = output_prefix if output_prefix else f"{column}_"

    try:
        result = df.copy()

        if group_by and group_by in df.columns:
            # Statistiche per gruppo
            agg_dict = {}
            for stat_name in stats:
                if stat_name in stat_funcs:
                    agg_dict[f"{prefix}{stat_name}"] = (column, stat_funcs[stat_name])

            # Calcola con NamedAgg
            grouped = df.groupby(group_by, dropna=False).agg(**agg_dict)
            result = result.merge(grouped, on=group_by, how="left")

        else:
            # Statistiche globali — aggiungi come colonna costante
            series = df[column]
            for stat_name in stats:
                col_name = f"{prefix}{stat_name}"
                func = stat_funcs.get(stat_name)
                if func is None:
                    continue
                if callable(func):
                    result[col_name] = func(series)
                else:
                    result[col_name] = getattr(series, func)()

        computed = [s for s in stats if s in stat_funcs]
        logger.info(
            f"Statistics completato: {len(computed)} statistiche su '{column}'"
            + (f" raggruppate per '{group_by}'" if group_by else "")
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Statistics: {e}", exc_info=True)
        raise


# Phase 5 B1.3b cutover: op_string_extract moved to `transforms.strings`
# (re-exported in testa al file via facade).


# Phase 5 B1.2 cutover: op_cross_join moved to `transforms.joins`
# (re-exported in testa al file via facade). Sezione lasciata vuota
# per preservare l'ancoraggio storico delle linee successive.


# =============================================================================
# TRANSPOSE — Trasponi righe e colonne
# Scambia righe e colonne del DataFrame.
# =============================================================================

def op_transpose(
    df: DataFrame,
    id_column: str = None
) -> DataFrame:
    """
    Trasposizione del DataFrame (righe ↔ colonne).

    Args:
        df: DataFrame input
        id_column: Colonna da usare come intestazioni delle nuove colonne.
                   Se None, traspone con indice numerico.

    Returns:
        DataFrame trasposto
    """
    if len(df) == 0:
        return df

    try:
        original_shape = df.shape

        if id_column and id_column in df.columns:
            # Usa la colonna indicata come nuove intestazioni
            result = df.set_index(id_column).T.reset_index()
            result = result.rename(columns={"index": id_column})
        else:
            result = df.T.reset_index()
            result.columns = [f"col_{i}" for i in range(len(result.columns))]

        logger.info(
            f"Transpose completato: {original_shape} -> {result.shape}"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Transpose: {e}", exc_info=True)
        raise


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


# =============================================================================
# DEDUP ADVANCED — Deduplicazione avanzata con ordinamento prioritario
# Rimuove duplicati con possibilità di ordinare prima per priorità.
# =============================================================================

def op_dedup_advanced(
    df: DataFrame,
    subset: List[str] = None,
    keep: str = "first",
    order_column: str = None,
    ascending: bool = True
) -> DataFrame:
    """
    Deduplicazione avanzata con ordinamento prioritario.

    Permette di specificare una colonna di ordinamento per determinare
    quale riga mantenere tra i duplicati.

    Args:
        df: DataFrame input
        subset: Colonne su cui verificare i duplicati (default: tutte)
        keep: Quale riga mantenere: 'first' o 'last'
        order_column: Colonna di ordinamento (applicato prima della deduplicazione)
        ascending: Ordine crescente (True) o decrescente (False)

    Returns:
        DataFrame deduplicato
    """
    if len(df) == 0:
        return df

    try:
        result = df.copy()
        original_len = len(result)

        # Ordina prima della deduplicazione se richiesto
        if order_column and order_column in result.columns:
            result.sort_values(by=order_column, ascending=ascending, inplace=True)

        # Filtra subset a colonne esistenti
        valid_subset = None
        if subset:
            valid_subset = [c for c in subset if c in result.columns]
            if not valid_subset:
                valid_subset = None

        result.drop_duplicates(subset=valid_subset, keep=keep, inplace=True)
        result.reset_index(drop=True, inplace=True)

        logger.info(
            f"DedupAdvanced completato: {original_len} -> {len(result)} righe "
            f"(rimossi {original_len - len(result)} duplicati)"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore DedupAdvanced: {e}", exc_info=True)
        raise


# =============================================================================
# WINDOW FUNCTION — Funzioni finestra: rank, cumsum, lag, lead, etc.
# Calcola funzioni analitiche per gruppo con ordinamento.
# =============================================================================

def op_window_function(
    df: DataFrame,
    column: str,
    function: str = "rank",
    group_by: str = None,
    order_column: str = None,
    offset: int = 1,
    output_column: str = None
) -> DataFrame:
    """
    Funzioni finestra (window functions) su colonne numeriche.

    Supporta rank, dense_rank, cumsum, cumcount, lag, lead, pct_change,
    running_mean. Opzionalmente applicabili per gruppo.

    Args:
        df: DataFrame input
        column: Colonna su cui applicare la funzione
        function: Tipo di funzione finestra
        group_by: Colonna di raggruppamento (opzionale)
        order_column: Colonna di ordinamento (per rank, lag, lead)
        offset: Offset per lag/lead (default: 1)
        output_column: Nome colonna di output (default: column + '_' + function)

    Returns:
        DataFrame con colonna finestra aggiunta
    """
    if len(df) == 0:
        return df

    if column not in df.columns:
        logger.warning(f"WindowFunction: colonna '{column}' non trovata")
        return df

    valid_functions = (
        "rank", "dense_rank", "cumsum", "cumcount",
        "lag", "lead", "pct_change", "running_mean"
    )
    if function not in valid_functions:
        logger.warning(
            f"WindowFunction: funzione '{function}' non valida. "
            f"Valori ammessi: {valid_functions}"
        )
        return df

    try:
        result = df.copy()
        out_col = output_column if output_column else f"{column}_{function}"

        # Ordina se specificato (importante per rank, lag, lead)
        if order_column and order_column in result.columns:
            result.sort_values(by=order_column, inplace=True)
            result.reset_index(drop=True, inplace=True)

        if group_by and group_by in result.columns:
            grouped = result.groupby(group_by)[column]
        else:
            grouped = result[column]

        if function == "rank":
            if group_by and group_by in result.columns:
                result[out_col] = result.groupby(group_by)[column].rank(method="min")
            else:
                result[out_col] = result[column].rank(method="min")
        elif function == "dense_rank":
            if group_by and group_by in result.columns:
                result[out_col] = result.groupby(group_by)[column].rank(method="dense")
            else:
                result[out_col] = result[column].rank(method="dense")
        elif function == "cumsum":
            if group_by and group_by in result.columns:
                result[out_col] = result.groupby(group_by)[column].cumsum()
            else:
                result[out_col] = result[column].cumsum()
        elif function == "cumcount":
            if group_by and group_by in result.columns:
                result[out_col] = result.groupby(group_by).cumcount()
            else:
                result[out_col] = range(len(result))
        elif function == "lag":
            if group_by and group_by in result.columns:
                result[out_col] = result.groupby(group_by)[column].shift(offset)
            else:
                result[out_col] = result[column].shift(offset)
        elif function == "lead":
            if group_by and group_by in result.columns:
                result[out_col] = result.groupby(group_by)[column].shift(-offset)
            else:
                result[out_col] = result[column].shift(-offset)
        elif function == "pct_change":
            if group_by and group_by in result.columns:
                result[out_col] = result.groupby(group_by)[column].pct_change()
            else:
                result[out_col] = result[column].pct_change()
        elif function == "running_mean":
            if group_by and group_by in result.columns:
                result[out_col] = result.groupby(group_by)[column].expanding().mean().reset_index(level=0, drop=True)
            else:
                result[out_col] = result[column].expanding().mean()

        logger.info(
            f"WindowFunction completato: {function}('{column}') -> '{out_col}'"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore WindowFunction: {e}", exc_info=True)
        raise


# Phase 5 B1.3c cutover: op_uuid_generator moved to `transforms.utility`
# (re-exported in testa al file via facade).
