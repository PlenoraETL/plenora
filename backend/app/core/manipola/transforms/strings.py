"""
Cluster STRINGS — operazioni di utilita' su colonne stringa.

Origine
=======
Estratto da `app.core.manipola.transforms.base` come parte di Phase 5
Batch B1.3a (cluster split, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1 facade pattern).

Contenuto B1.3a:
  - `op_string_pad`   — padding a larghezza fissa (left/right).
  - `op_string_length` — colonna lunghezza per riga.

Cluster espandibile (B1.3b previsto):
  - `op_string_extract` — estrazione substring/regex (resta in base.py
    fino a B1.3b).

Invarianti pubbliche preservate (regola §6.1 zero call-site changes):
  - `from app.core.manipola.transforms.base import op_string_pad` /
    `op_string_length` continuano a funzionare grazie al facade
    re-export in `base.py`.
  - `transforms_registry.json` con entries `"base.op_string_pad"` /
    `"base.op_string_length"` continua a risolvere via
    `getattr(<base module>, "<func>")` dal `PluginExecutor`.
  - Nuovo path canonico `from app.core.manipola.transforms.strings
    import op_string_pad, op_string_length` disponibile.

Logger naming
=============
Il logger e' creato esplicitamente come
`"app.core.manipola.transforms.base"` (NON `__name__`) per preservare
il contratto osservabile pre-extraction: i record `[STRING PAD]` /
`[CRASH] Errore StringPad` / `[CRASH] Errore StringLength` continuano
ad apparire sotto lo stesso logger name dei consumer/log aggregation.
Stesso pattern del fix logger in `joins.py` (B1.1b).

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`base.py` al momento di B1.3a. Nessuna modifica di logica, comment,
log payload, o ordine di operazione.
"""

import pandas as pd
import geopandas as gpd
from typing import Union

from app.utils.logger import get_logger

# Stesso logger name di `base.py`: i record sono indistinguibili dai
# consumer che attaccano handler / filtrano per
# "app.core.manipola.transforms.base" (pattern §6.1 facade).
logger = get_logger("app.core.manipola.transforms.base")

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# =============================================================================
# STRING PAD — Riempimento stringa a larghezza fissa
# Aggiunge caratteri di riempimento a sinistra o a destra.
# =============================================================================

def op_string_pad(
    df: DataFrame,
    column: str,
    width: int = 5,
    side: str = "left",
    fill_char: str = "0",
    output_column: str = None
) -> DataFrame:
    """
    Riempimento (padding) di una colonna stringa a larghezza fissa.

    Args:
        df: DataFrame input
        column: Colonna da elaborare
        width: Larghezza finale della stringa
        side: Lato del riempimento: 'left' o 'right'
        fill_char: Carattere di riempimento (default '0')
        output_column: Colonna di output (default: sovrascrive la colonna originale)

    Returns:
        DataFrame con colonna paddata
    """
    if len(df) == 0:
        return df

    if column not in df.columns:
        logger.warning(f"StringPad: colonna '{column}' non trovata")
        return df

    try:
        result = df.copy()
        out_col = output_column if output_column else column
        text_series = result[column].astype(str)

        if side == "left":
            result[out_col] = text_series.str.pad(width, side="left", fillchar=fill_char)
        else:
            result[out_col] = text_series.str.pad(width, side="right", fillchar=fill_char)

        logger.info(
            f"StringPad completato: colonna='{column}', larghezza={width}, "
            f"lato='{side}', output='{out_col}'"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore StringPad: {e}", exc_info=True)
        raise


# =============================================================================
# STRING LENGTH — Calcola la lunghezza delle stringhe
# Aggiunge una colonna con la lunghezza di ogni valore.
# =============================================================================

def op_string_length(
    df: DataFrame,
    column: str,
    output_column: str = None
) -> DataFrame:
    """
    Calcola la lunghezza delle stringhe in una colonna.

    Args:
        df: DataFrame input
        column: Colonna di cui calcolare la lunghezza
        output_column: Nome della colonna di output (default: column + '_length')

    Returns:
        DataFrame con colonna lunghezza aggiunta
    """
    if len(df) == 0:
        return df

    if column not in df.columns:
        logger.warning(f"StringLength: colonna '{column}' non trovata")
        return df

    try:
        result = df.copy()
        out_col = output_column if output_column else f"{column}_length"

        result[out_col] = result[column].astype(str).str.len()

        logger.info(f"StringLength completato: '{column}' -> '{out_col}'")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore StringLength: {e}", exc_info=True)
        raise
