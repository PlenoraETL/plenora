"""
Cluster UTILITY — operazioni di colonna derivata per-riga.

Keeps transformation behavior isolated from orchestration callers.
"""

import uuid

from typing import List, Optional

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.ports import FrameLike
from app.core.dataframes.tabular import (
    coerce_datetime_series,
    group_cumcount_series,
    sort_rows_reset_index,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

DataFrame = FrameLike


# NUMERAZIONE RIGHE
# Aggiunge colonna con numero progressivo, opzionalmente partizionato e ordinato

def op_add_row_number(
    df: DataFrame,
    output_column: str = "row_number",
    start: int = 1,
    partition_column: Optional[str] = None,
    order_column: Optional[str] = None,
    ascending: bool = True
) -> DataFrame:
    """
    Aggiunge colonna con numero di riga sequenziale.

    Args:
        df: DataFrame input
        output_column: Nome della colonna da creare
        start: Valore iniziale della numerazione
        partition_column: Colonna per numerazione all'interno di gruppi
        order_column: Colonna per ordinamento prima della numerazione
        ascending: Direzione ordinamento (True = crescente)

    Returns:
        DataFrame con colonna di numerazione
    """
    try:
        result = copy_frame_for_update(df)

        # Ordinamento opzionale
        if order_column and order_column in result.columns:
            result = sort_rows_reset_index(
                result,
                by=order_column,
                ascending=ascending,
            )

        if partition_column and partition_column in result.columns:
            # Numerazione all'interno di ogni gruppo
            result[output_column] = group_cumcount_series(
                result,
                partition_column,
                start,
            )
        else:
            # Numerazione globale
            result[output_column] = range(start, start + len(result))

        logger.info(f"AddRowNumber: colonna '{output_column}' aggiunta ({start} -> {start + len(result) - 1})")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore AddRowNumber: {e}", exc_info=True)
        raise


# ESTRAZIONE PARTI DATA
# Estrae componenti (anno, mese, giorno, ecc.) da una colonna datetime

def op_date_extract(
    df: DataFrame,
    column: str,
    parts: Optional[List[str]] = None,
    prefix: str = ""
) -> DataFrame:
    """
    Estrae componenti da una colonna data/datetime.

    Args:
        df: DataFrame input
        column: Colonna datetime sorgente
        parts: Lista di parti da estrarre (year, month, day, quarter, weekday,
               week, hour, minute, second). Default: ["year"]
        prefix: Prefisso per i nomi colonna. Se vuoto usa "{column}_"

    Returns:
        DataFrame con colonne estratte
    """
    if parts is None:
        parts = ["year"]

    if column not in df.columns:
        logger.warning(f"DateExtract: colonna '{column}' non trovata")
        return df

    try:
        result = copy_frame_for_update(df)

        # Conversione a datetime
        dt_col = coerce_datetime_series(result[column])

        # Prefisso per le colonne output
        col_prefix = prefix if prefix else f"{column}_"

        extractors = {
            'year': lambda s: s.dt.year,
            'month': lambda s: s.dt.month,
            'day': lambda s: s.dt.day,
            'quarter': lambda s: s.dt.quarter,
            'weekday': lambda s: s.dt.weekday,
            'week': lambda s: s.dt.isocalendar().week.astype('Int64'),
            'hour': lambda s: s.dt.hour,
            'minute': lambda s: s.dt.minute,
            'second': lambda s: s.dt.second,
        }

        created = []
        for part in parts:
            part_lower = part.lower().strip()
            if part_lower in extractors:
                col_name = f"{col_prefix}{part_lower}"
                result[col_name] = extractors[part_lower](dt_col)
                created.append(col_name)
            else:
                logger.warning(f"DateExtract: parte '{part}' non supportata")

        logger.info(f"DateExtract: create {len(created)} colonne da '{column}': {created}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore DateExtract: {e}", exc_info=True)
        raise


# UUID GENERATOR — Genera un UUID univoco per ogni riga
# Utile per creare chiavi primarie sintetiche

def op_uuid_generator(
    df: DataFrame,
    output_column: str = "uuid"
) -> DataFrame:
    """
    Genera un UUID v4 univoco per ogni riga del DataFrame.

    Args:
        df: DataFrame input
        output_column: Nome della colonna di output (default: 'uuid')

    Returns:
        DataFrame con colonna UUID aggiunta
    """
    if len(df) == 0:
        return df

    try:
        result = copy_frame_for_update(df)
        result[output_column] = [str(uuid.uuid4()) for _ in range(len(result))]

        logger.info(
            f"UUIDGenerator completato: {len(result)} UUID generati "
            f"in colonna '{output_column}'"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore UUIDGenerator: {e}", exc_info=True)
        raise
