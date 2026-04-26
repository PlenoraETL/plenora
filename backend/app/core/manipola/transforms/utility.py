"""
Cluster UTILITY — operazioni di colonna derivata per-riga.

Origine
=======
Estratto da `app.core.manipola.transforms.base` come parte di Phase 5
Batch B1.3c (cluster split, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1 facade pattern).

Contenuto:
  - `op_add_row_number` — colonna progressiva (globale o per partizione,
                          con eventuale ordinamento prima della numerazione).
  - `op_date_extract`   — estrazione componenti (year/month/day/quarter/
                          weekday/week/hour/minute/second) da una colonna
                          datetime.
  - `op_uuid_generator` — UUID v4 random per riga (chiavi sintetiche).

Razionale del raggruppamento: tutte e tre creano una **colonna derivata
per-riga** senza trasformare i dati di input esistenti (non sono
filtri, join, aggregati, normalizzazioni). Singolo cluster cohesive.

Invarianti pubbliche preservate (regola §6.1 zero call-site changes):
  - `from app.core.manipola.transforms.base import <op>` continua a
    funzionare per tutte e 3 le funzioni grazie al facade re-export
    in `base.py`.
  - Nessuna delle 3 funzioni e' esposta da `transforms/__init__.py`
    (verificato pre-extraction) → nessun cambio init richiesto.
  - `transforms_registry.json` con entries `"base.op_add_row_number"` /
    `"base.op_date_extract"` / `"base.op_uuid_generator"` continua a
    risolvere via `getattr(<base module>, "<func>")` dal `PluginExecutor`.
  - Nuovo path canonico `from app.core.manipola.transforms.utility
    import <op>` disponibile.

Logger naming
=============
Logger creato esplicitamente come
`"app.core.manipola.transforms.base"` (NON `__name__`) per preservare
il contratto osservabile pre-extraction: i record `AddRowNumber`,
`DateExtract`, `UUIDGenerator` (incluso `[CRASH] ...`) continuano ad
apparire sotto il logger name dei consumer. Stesso pattern di
`joins.py` (B1.1b) e `strings.py` (B1.3a).

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`base.py` al momento di B1.3c. Nessuna modifica di logica, comment,
log payload, o ordine di operazione.
"""

import uuid

import pandas as pd
import geopandas as gpd
from typing import List, Optional, Union

from app.utils.logger import get_logger

# Stesso logger name di `base.py`: i record sono indistinguibili dai
# consumer che attaccano handler / filtrano per
# "app.core.manipola.transforms.base" (pattern §6.1 facade).
logger = get_logger("app.core.manipola.transforms.base")

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# =============================================================================
# NUMERAZIONE RIGHE
# Aggiunge colonna con numero progressivo, opzionalmente partizionato e ordinato.
# =============================================================================

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
        result = df.copy()

        # Ordinamento opzionale
        if order_column and order_column in result.columns:
            result.sort_values(by=order_column, ascending=ascending, inplace=True)
            result.reset_index(drop=True, inplace=True)

        if partition_column and partition_column in result.columns:
            # Numerazione all'interno di ogni gruppo
            result[output_column] = result.groupby(partition_column).cumcount() + start
        else:
            # Numerazione globale
            result[output_column] = range(start, start + len(result))

        logger.info(f"AddRowNumber: colonna '{output_column}' aggiunta ({start} -> {start + len(result) - 1})")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore AddRowNumber: {e}", exc_info=True)
        raise


# =============================================================================
# ESTRAZIONE PARTI DATA
# Estrae componenti (anno, mese, giorno, ecc.) da una colonna datetime.
# =============================================================================

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
        result = df.copy()

        # Conversione a datetime
        dt_col = pd.to_datetime(result[column], errors='coerce')

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


# =============================================================================
# UUID GENERATOR — Genera un UUID univoco per ogni riga
# Utile per creare chiavi primarie sintetiche.
# =============================================================================

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
        result = df.copy()
        result[output_column] = [str(uuid.uuid4()) for _ in range(len(result))]

        logger.info(
            f"UUIDGenerator completato: {len(result)} UUID generati "
            f"in colonna '{output_column}'"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore UUIDGenerator: {e}", exc_info=True)
        raise
