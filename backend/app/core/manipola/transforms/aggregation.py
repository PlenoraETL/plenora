"""
Cluster AGGREGATION — operazioni analytics: group-by, sort, dedup, window.

Origine
=======
Estratto da `app.core.manipola.transforms.base` come parte di Phase 5
Batch B1.7 (cluster split, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1 facade pattern).

Razionale del raggruppamento: tutte le 5 funzioni sono operazioni
**analytics che operano su gruppi/ordine di righe**:
  - `op_aggregate`        — group-by + funzioni di aggregazione
                            (count/sum/mean/min/max/first/last/concat).
  - `op_sort`             — ordinamento multi-colonna.
  - `op_distinct`         — deduplicazione semplice (subset/keep).
  - `op_dedup_advanced`   — deduplicazione con ordinamento prioritario
                            (sort_then_dedup).
  - `op_window_function`  — funzioni finestra (rank/dense_rank/cumsum/
                            cumcount/lag/lead/pct_change/running_mean)
                            opzionalmente per gruppo.

Tutte e 5 condividono la mental-model "calcoli su un sottoinsieme
ordinato di righe" — il dominio piu' coeso rimasto in `base.py` post
B1.6.

Phase 4 A1 ha applicato `dropna=False` a tutti i `groupby(...)` di
`op_aggregate` per non perdere righe con NaN nelle chiavi.
Comportamento preservato bit-per-bit qui.

Contenuto:
  - `op_aggregate`        — 5 path (count puro, count fallback, agg dict,
                            con concat custom, con multi-aggregation).
  - `op_sort`             — sort_values + reset_index.
  - `op_distinct`         — drop_duplicates con `keep='false'` mappato
                            a `keep=False` di pandas.
  - `op_dedup_advanced`   — sort_values opzionale + drop_duplicates +
                            reset_index.
  - `op_window_function`  — switch su 8 funzioni con/senza groupby.

Invarianti pubbliche preservate (regola §6.1 zero call-site changes):
  - `from app.core.manipola.transforms.base import <op>` continua a
    funzionare per tutte e 5 grazie al facade re-export in `base.py`.
  - `from app.core.manipola.transforms import op_aggregate` /
    `op_sort` / `op_distinct` continuano a funzionare (`__init__.py`
    le re-exporta gia' via `from .base import ...` che cattura il
    facade). `op_dedup_advanced` e `op_window_function` non sono in
    init (verificato pre-extraction) → nessun cambio init richiesto.
  - `transforms_registry.json` con entries `"base.op_*"` per tutte e 5
    continua a risolvere via `getattr(<base module>, "<func>")` dal
    `PluginExecutor`.
  - Nuovo path canonico `from app.core.manipola.transforms.aggregation
    import <op>` disponibile.

Logger naming
=============
Logger creato esplicitamente come
`"app.core.manipola.transforms.base"` (NON `__name__`) per preservare
il contratto osservabile pre-extraction: i record `op_aggregate
CALLED`, `Aggregate (count)`, `Aggregate: ...`, `[CRASH] Errore
Aggregate`, `[CRASH] Errore Sort`, `Distinct: ...`,
`DedupAdvanced completato`, `WindowFunction completato`,
`[CRASH] Errore WindowFunction` continuano ad apparire sotto il
logger name dei consumer. Stesso pattern di tutti i moduli figli
estratti nei batch B1.1-B1.6.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`base.py` al momento di B1.7. Nessuna modifica di logica, comment,
log payload, ordine di operazione, fail-closed semantics. La nested
function `make_concat_agg` resta locale a `op_aggregate` come
factory closure (hot-path: una nuova closure per ogni concat
configurato).
"""

import pandas as pd
import geopandas as gpd
from typing import Dict, List, Union

from app.utils.logger import get_logger

logger = get_logger("app.core.manipola.transforms.base")

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# AGGREGAZIONE (GROUP BY)
# Raggruppa per colonne e applica funzioni: count, sum, mean, min, max, first, last.

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


# ORDINAMENTO
# Ordina DataFrame per una o più colonne (asc/desc).

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


# DEDUPLICAZIONE
# Rimuove righe duplicate su colonne selezionate. Default: tutte le colonne.

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


# DEDUP ADVANCED — Deduplicazione avanzata con ordinamento prioritario
# Rimuove duplicati con possibilità di ordinare prima per priorità.

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


# WINDOW FUNCTION — Funzioni finestra: rank, cumsum, lag, lead, etc.
# Calcola funzioni analitiche per gruppo con ordinamento.

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
