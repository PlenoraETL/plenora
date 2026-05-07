"""
Cluster COLUMNS — operazioni di shaping/managing della struttura colonne.

Keeps transformation behavior isolated from orchestration callers.
"""

import numpy as np
import pandas as pd
import geopandas as gpd
from typing import Dict, List, Union

from app.utils.logger import get_logger

logger = get_logger("app.core.manipola.transforms.base")

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


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
# Rinomina colonne secondo mapping {vecchio_nome: nuovo_nome}

def op_rename(
    df: DataFrame,
    old_name: str = None,
    new_name: str = None,
    renames: List[Dict[str, str]] = None
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
        result = df.copy()

        if columns:
            # Filtra solo colonne che esistono effettivamente
            valid_cols = [c for c in columns if c in result.columns]
            # Aggiungi le colonne rimanenti non specificate
            remaining = [c for c in result.columns if c not in valid_cols]
            new_order = valid_cols + remaining
            result = result[new_order]
        elif sort_alphabetical:
            result = result[sorted(result.columns)]

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

    result = df.copy()

    try:
        subset = result[valid_cols]

        if skip_null:
            # Vera vettorizzazione: estrazione numpy arrays per colonna
            # + Python list-comp con itertools.compress per riga
            # Il path precedente `.agg(lambda parts: ..., axis=1)` era
            # Python row-iter mascherata dentro pandas agg: ~7x piu' lento
            # del naive apply. compress e' C-implemented e lavora su
            # valori numpy direttamente, senza costruire una Series-per-riga
            n = len(subset)
            if n == 0:
                result[output_column] = pd.Series([], dtype=object)
                return result

            # numpy str arrays + notna masks, una volta per colonna
            # subset.reset_index(drop=True) non necessario: lavoriamo
            # direttamente sugli ndarray, insensibili all'indice
            str_arrs = [
                subset[c].astype(str).to_numpy() for c in valid_cols
            ]
            mask_arrs = [subset[c].notna().to_numpy() for c in valid_cols]

            # Per-row join: compress(values, mask) filtra in C le posizioni
            # dove mask=True; separator.join lavora solo su stringhe
            # Semantica legacy preservata: join "" → None (copre sia riga
            # all-NaN che join di un unico ""
            from itertools import compress  # import locale: hot path
            out = [None] * n
            for i, (row_vals, row_mask) in enumerate(
                zip(zip(*str_arrs), zip(*mask_arrs))
            ):
                joined = separator.join(compress(row_vals, row_mask))
                if joined != "":
                    out[i] = joined
            result[output_column] = np.array(out, dtype=object)
        else:
            # NULL come stringa vuota (skip_null=False). Pandas 3.0 puo'
            # preservare NaN/NaT su dtype numerici/datetime anche dopo
            # `fillna("")` o `where(notna, "")` perche' la coercion
            # implicita a object dtype e' stata rimossa: i sostitutivi
            # stringa vengono silenziosamente non-applicati e il
            # successivo `astype(str)` non converte affidabilmente NaN
            # Fix bulletproof: conversione per-cella che bypassa la
            # type-coercion DataFrame e gestisce esplicitamente NaN/NaT
            def _cell_to_str(x):
                return "" if pd.isna(x) else str(x)

            result[output_column] = (
                subset.map(_cell_to_str).agg(separator.join, axis=1)
            )

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

    result = df.copy()

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
