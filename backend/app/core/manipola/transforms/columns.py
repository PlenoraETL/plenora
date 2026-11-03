"""
Cluster COLUMNS — operazioni di shaping/managing della struttura colonne.

Keeps transformation behavior isolated from orchestration callers.
"""

from itertools import compress

import numpy as np
from typing import List

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.tabular import empty_object_series, join_columns_as_strings
from app.core.dataframes.ports import FrameLike
from app.core.manipola.types import JsonRows
from app.utils.logger import get_logger
from app.shared.execution.column_selection import select_column_plan

logger = get_logger(__name__)

DataFrame = FrameLike

# Dimensione del batch per concat_columns RAM-bounded.
# Per N_cols=12 * batch=100k * avg_str=50B = ~60MB peak per batch.
_CONCAT_BATCH_SIZE = 100_000


# RIMOZIONE COLONNE
# Rimuove colonne selezionate dal DataFrame

def op_drop_columns(
    df: DataFrame,
    columns: List[str]
) -> DataFrame:
    """
    Elimina colonne dal DataFrame.

    Args:
        df: DataFrame input
        columns: Lista colonne da eliminare

    Returns:
        DataFrame senza le colonne specificate
    """
    if not columns:
        return df

    # Filtra colonne esistenti
    existing = [c for c in columns if c in df.columns]

    if not existing:
        return df

    try:
        result = df.drop(columns=existing)
        return result
    except Exception as e:
        logger.error(f"[CRASH] Errore Drop Columns: {e}", exc_info=True)
        raise


# RINOMINA COLONNE
# Rinomina colonne secondo mapping {old_name: new_name}

def op_rename(
    df: DataFrame,
    old_name: str = None,
    new_name: str = None,
    renames: JsonRows = None
) -> DataFrame:
    """
    Rinomina una o più colonne.

    Args:
        df: DataFrame input
        old_name: Nome attuale colonna (per retrocompatibilità)
        new_name: Nuovo nome (per retrocompatibilità)
        renames: Lista di rinominazioni [{'old_name': 'x', 'new_name': 'y'}, ...]

    Returns:
        DataFrame con colonne rinominate
    """
    rename_map = {}

    # Supporto lista di rinominazioni
    if renames:
        for r in renames:
            old = r.get('old_name', '')
            new = r.get('new_name', '')
            if old and new and old in df.columns:
                rename_map[old] = new

    # Supporto singola rinominazione (retrocompatibilità
    elif old_name and new_name:
        if old_name in df.columns:
            rename_map[old_name] = new_name

    if not rename_map:
        logger.warning("Rename: nessuna colonna valida da rinominare")
        return df

    try:
        result = df.rename(columns=rename_map)
        logger.info(f"Rename: {len(rename_map)} colonne rinominate", op="transform")
        return result
    except Exception as e:
        logger.error(f"[CRASH] Errore Rename: {e}", exc_info=True)
        raise


# REORDER COLUMNS — Riordina le colonne del DataFrame
# Permette di specificare un ordine personalizzato o alfabetico

def op_reorder_columns(
    df: DataFrame,
    columns: List[str] = None,
    sort_alphabetical: bool = False
) -> DataFrame:
    """
    Riordina le colonne del DataFrame.

    Args:
        df: DataFrame input
        columns: Lista ordinata di nomi colonna. Le colonne non elencate
                 vengono aggiunte in coda nell'ordine originale.
        sort_alphabetical: Se True, ordina le colonne alfabeticamente
                           (ignorato se columns è fornito)

    Returns:
        DataFrame con colonne riordinate
    """
    if len(df) == 0:
        return df

    try:
        # Le subset di colonne pandas sono sufficienti qui: non facciamo
        # modifiche column-level, quindi niente copia profonda.
        if columns:
            valid_cols = [c for c in columns if c in df.columns]
            remaining = [c for c in df.columns if c not in valid_cols]
            new_order = valid_cols + remaining
            result = df[new_order]
        elif sort_alphabetical:
            result = df[sorted(df.columns)]
        else:
            # Nessuna riordinazione richiesta: ritorna df invariato.
            result = df

        logger.info(f"ReorderColumns completato: {list(result.columns)}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore ReorderColumns: {e}", exc_info=True)
        raise


# CONCATENAZIONE COLONNE
# Unisce più colonne in una nuova colonna con separatore

def op_concat_columns(
    df: DataFrame,
    columns: List[str],
    output_column: str = "concatenated",
    separator: str = " ",
    skip_null: bool = True
) -> DataFrame:
    """
    Concatena più colonne in una nuova colonna.

    Args:
        df: DataFrame input
        columns: Lista colonne da concatenare (in ordine)
        output_column: Nome colonna risultato
        separator: Separatore tra valori (default " ")
        skip_null: Se True, salta valori NULL; se False, li tratta come stringa vuota

    Returns:
        DataFrame con nuova colonna concatenata

    Esempio:
        columns=["cognome", "nome"], separator=" " → "ROSSI MARIO"
    """
    if not columns:
        logger.warning("concat_columns: nessuna colonna specificata")
        return df

    # Filtra colonne esistenti
    valid_cols = [c for c in columns if c in df.columns]
    if not valid_cols:
        logger.warning(f"concat_columns: nessuna colonna valida tra {columns}")
        return df

    result = copy_frame_for_update(df)

    try:
        subset = result[valid_cols]

        if skip_null:
            # Batch RAM-bounded: processa `_CONCAT_BATCH_SIZE` righe per
            # volta. Peak ~ N_cols * batch_size * avg_str_size.
            #
            # Vera vettorizzazione: estrazione numpy arrays per colonna
            # + Python list-comp con itertools.compress per riga
            # Il path precedente `.agg(lambda parts: ..., axis=1)` era
            # Python row-iter mascherata dentro pandas agg: ~7x piu' lento
            # del naive apply. compress e' C-implemented e lavora su
            # valori numpy direttamente, senza costruire una Series-per-riga
            n = len(subset)
            if n == 0:
                result[output_column] = empty_object_series()
                return result

            out = np.empty(n, dtype=object)
            out.fill(None)

            for batch_start in range(0, n, _CONCAT_BATCH_SIZE):
                batch_end = min(batch_start + _CONCAT_BATCH_SIZE, n)

                # Solo i bytes del batch per ogni colonna.
                str_arrs_batch = [
                    subset[c].iloc[batch_start:batch_end].astype(str).to_numpy()
                    for c in valid_cols
                ]
                mask_arrs_batch = [
                    subset[c].iloc[batch_start:batch_end].notna().to_numpy()
                    for c in valid_cols
                ]

                # Per-row join: compress(values, mask) filtra in C le posizioni
                # dove mask=True. Join vuoto -> None.
                for j, (row_vals, row_mask) in enumerate(
                    zip(zip(*str_arrs_batch), zip(*mask_arrs_batch))
                ):
                    joined = separator.join(compress(row_vals, row_mask))
                    if joined != "":
                        out[batch_start + j] = joined

                # Free batch arrays prima del prossimo iter.
                del str_arrs_batch, mask_arrs_batch

            result[output_column] = out
        else:
            result[output_column] = join_columns_as_strings(subset, separator)

        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore concat_columns: {e}", exc_info=True)
        raise


# DIVISIONE COLONNA
# Divide colonna su separatore e crea N nuove colonne

def op_split_column(
    df: DataFrame,
    column: str,
    delimiter: str = ",",
    new_columns: List[str] = None,
    max_splits: int = -1
) -> DataFrame:
    """
    Divide una colonna in più colonne usando un separatore.

    Args:
        df: DataFrame input
        column: Colonna da dividere
        delimiter: Carattere separatore
        new_columns: Nomi nuove colonne (None = auto)
        max_splits: Numero max divisioni (-1 = tutte)

    Returns:
        DataFrame con colonne aggiuntive
    """
    if not column or column not in df.columns:
        return df

    result = copy_frame_for_update(df)

    try:
        n_splits = max_splits if max_splits > 0 else None
        split_result = result[column].astype(str).str.split(delimiter, n=n_splits, expand=True)

        if new_columns:
            for i, new_col in enumerate(new_columns):
                if i < split_result.shape[1]:
                    result[new_col] = split_result[i]
        else:
            for i in range(split_result.shape[1]):
                result[f"{column}_part{i+1}"] = split_result[i]

        logger.info(f"Split column: {column} -> {split_result.shape[1]} parti")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Split Column: {e}", exc_info=True)
        raise


def op_select_columns(df: DataFrame, columns: JsonRows | None = None) -> DataFrame:
    """Seleziona, ordina e rinomina preservando le colonne upstream nuove."""
    plan = select_column_plan(list(df.columns), columns if columns is not None else [])
    return df[[name for name, _ in plan]].rename(columns=dict(plan))
