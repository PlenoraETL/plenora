"""
Cluster AGGREGATION — operazioni analytics: group-by, sort, dedup, window.

Keeps transformation behavior isolated from orchestration callers.
"""

from typing import List

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.ports import FrameLike
from app.core.dataframes.tabular import (
    drop_duplicate_rows_reset_index,
    group_aggregate_reset,
    group_size_reset,
    is_multiindex,
    sort_rows_reset_index,
    window_function_values,
)
from app.core.dataframes.values import is_null_scalar
from app.core.manipola.types import JsonRows, StringMap
from app.utils.logger import get_logger

logger = get_logger(__name__)

DataFrame = FrameLike


# AGGREGAZIONE (GROUP BY
# Raggruppa per colonne e applica funzioni: count, sum, mean, min, max, first, last

def op_aggregate(
    df: DataFrame,
    group_by: List[str],
    aggregations: JsonRows = None
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
        # Se nessuna aggregazione, solo count
        # dropna=False: includiamo i gruppi con NaN nelle chiavi per non
        # perdere righe silenziosamente (default pandas e' dropna=True che
        # le scarta — comportamento sorprendente per chi aggrega
        if not aggregations:
            result = group_size_reset(df, group_by, name="count", dropna=False)
            logger.info(f"Aggregate (count): {len(df)} -> {len(result)} righe")
            return result

        # Funzione concat personalizzata
        def make_concat_agg(separator=", ", distinct=False, skip_null=True):
            def concat_agg(values):
                vals = list(values)
                if skip_null:
                    vals = [v for v in vals if v is not None and not is_null_scalar(v)]
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
            logger.warning("agg_dict vuoto dopo parsing aggregazioni! Fallback a count")
            result = group_size_reset(df, group_by, name="count", dropna=False)
        else:
            logger.info(f"agg_dict finale: {agg_dict}")
            result = group_aggregate_reset(df, group_by, agg_dict, dropna=False)
            # Flatten multi-index columns
            if is_multiindex(result.columns):
                result.columns = ['_'.join(col).strip('_') for col in result.columns]

        # Alias utente-friendly per le colonne aggregate. Senza, la
        # colonna output mantiene il nome originale (es. `id` per
        # COUNT(id)) o `<col>_<func>` (MultiIndex flat), entrambi
        # poco leggibili. Il FE permette di specificare `alias`
        # nell'AggregationListInput; lo applichiamo qui post-agg.
        renames: StringMap = {}
        for agg in aggregations:
            alias = (agg.get("alias") or "").strip()
            if not alias:
                continue
            col = agg.get("column")
            func = (agg.get("function") or "").lower()
            # Caso single agg per colonna → output col == col originale
            if col in result.columns and col not in group_by:
                renames[col] = alias
                continue
            # Caso multi-agg → MultiIndex flat `<col>_<func>`
            flat_name = f"{col}_{func}"
            if flat_name in result.columns:
                renames[flat_name] = alias
        if renames:
            result = result.rename(columns=renames)

        logger.info(f"Aggregate: {len(df)} -> {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Aggregate: {e}", exc_info=True)
        raise


# ORDINAMENTO
# Ordina DataFrame per una o più colonne (asc/desc

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
        result = sort_rows_reset_index(df, by=columns, ascending=ascending)
        return result
    except Exception as e:
        logger.error(f"[CRASH] Errore Sort: {e}", exc_info=True)
        raise


# DEDUPLICAZIONE
# Rimuove righe duplicate su colonne selezionate. Default: tutte le colonne

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

        result = drop_duplicate_rows_reset_index(
            df,
            subset=subset,
            keep=keep_val,
        )
        logger.info(f"Distinct: {len(df)} -> {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Distinct: {e}", exc_info=True)
        raise


# DEDUP ADVANCED — Deduplicazione avanzata con ordinamento prioritario
# Rimuove duplicati con possibilità di ordinare prima per priorità

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
        # sort/drop_duplicates restituiscono sempre un nuovo DataFrame.
        result = df
        original_len = len(result)

        # Ordina prima della deduplicazione se richiesto
        if order_column and order_column in result.columns:
            result = sort_rows_reset_index(result, by=order_column, ascending=ascending)

        # Filtra subset a colonne esistenti
        valid_subset = None
        if subset:
            valid_subset = [c for c in subset if c in result.columns]
            if not valid_subset:
                valid_subset = None

        result = drop_duplicate_rows_reset_index(
            result,
            subset=valid_subset,
            keep=keep,
        )

        logger.info(
            f"DedupAdvanced completato: {original_len} -> {len(result)} righe "
            f"(rimossi {original_len - len(result)} duplicati)"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore DedupAdvanced: {e}", exc_info=True)
        raise


# WINDOW FUNCTION — Funzioni finestra: rank, cumsum, lag, lead, etc
# Calcola funzioni analitiche per gruppo con ordinamento

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
        result = copy_frame_for_update(df)
        out_col = output_column if output_column else f"{column}_{function}"

        # Ordina se specificato (importante per rank, lag, lead
        if order_column and order_column in result.columns:
            result.sort_values(by=order_column, inplace=True)
            result.reset_index(drop=True, inplace=True)

        if function == "rank":
            result[out_col] = window_function_values(
                result,
                column,
                function,
                group_by=group_by,
                offset=offset,
            )
        elif function == "dense_rank":
            result[out_col] = window_function_values(
                result,
                column,
                function,
                group_by=group_by,
                offset=offset,
            )
        elif function == "cumsum":
            result[out_col] = window_function_values(
                result,
                column,
                function,
                group_by=group_by,
                offset=offset,
            )
        elif function == "cumcount":
            result[out_col] = window_function_values(
                result,
                column,
                function,
                group_by=group_by,
                offset=offset,
            )
        elif function == "lag":
            result[out_col] = window_function_values(
                result,
                column,
                function,
                group_by=group_by,
                offset=offset,
            )
        elif function == "lead":
            result[out_col] = window_function_values(
                result,
                column,
                function,
                group_by=group_by,
                offset=offset,
            )
        elif function == "pct_change":
            result[out_col] = window_function_values(
                result,
                column,
                function,
                group_by=group_by,
                offset=offset,
            )
        elif function == "running_mean":
            result[out_col] = window_function_values(
                result,
                column,
                function,
                group_by=group_by,
                offset=offset,
            )

        logger.info(
            f"WindowFunction completato: {function}('{column}') -> '{out_col}'"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore WindowFunction: {e}", exc_info=True)
        raise
