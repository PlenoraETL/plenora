"""
Cluster STRINGS — operazioni di utilita' su colonne stringa.

Origine
=======
Estratto da `app.core.manipola.transforms.base` come parte di Phase 5
Batch B1.3a + B1.3b (cluster split, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1 facade pattern).

Contenuto:
  - `op_string_pad`     — padding a larghezza fissa (left/right). [B1.3a]
  - `op_string_length`  — colonna lunghezza per riga. [B1.3a]
  - `op_string_extract` — estrazione substring/regex (StringSearcher
                          equivalente FME, supporta gruppi nominati). [B1.3b]
  - `op_text_normalize` — operazioni testo (trim/lower/upper/title/
                          strip_accents/strip_double_spaces/full). [B1.3b]

Invarianti pubbliche preservate (regola §6.1 zero call-site changes):
  - `from app.core.manipola.transforms.base import <op>` continua a
    funzionare per tutte e 4 le funzioni (`op_string_pad`,
    `op_string_length`, `op_string_extract`, `op_text_normalize`)
    grazie al facade re-export in `base.py`.
  - `from app.core.manipola.transforms import op_string_extract` e
    `op_text_normalize` (gia' re-exported in `__init__.py`) continuano
    a funzionare: l'import in `__init__.py` resta `from .base import
    op_string_extract, op_text_normalize` e cattura il facade.
  - `transforms_registry.json` con entries `"base.<func>"` per tutte e
    4 continua a risolvere via `getattr(<base module>, "<func>")` dal
    `PluginExecutor`.
  - Nuovo path canonico `from app.core.manipola.transforms.strings
    import <op>` disponibile per tutte e 4.

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
from typing import List, Union

from app.utils.logger import get_logger

logger = get_logger("app.core.manipola.transforms.base")

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# STRING PAD — Riempimento stringa a larghezza fissa
# Aggiunge caratteri di riempimento a sinistra o a destra.

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


# STRING LENGTH — Calcola la lunghezza delle stringhe
# Aggiunge una colonna con la lunghezza di ogni valore.

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


# STRING EXTRACT — StringSearcher
# Estrae testo da una colonna tramite espressione regolare.

def op_string_extract(
    df: DataFrame,
    column: str,
    pattern: str,
    output_column: str = None,
    extract_all: bool = False
) -> DataFrame:
    """
    Estrae sottostringhe da una colonna di testo usando
    un'espressione regolare.

    Equivalente FME: StringSearcher.

    Supporta gruppi nominati (?P<nome>...) per creare più colonne,
    oppure un singolo gruppo di cattura per estrarre in una colonna.

    Args:
        df: DataFrame input
        column: Colonna di testo sorgente
        pattern: Espressione regolare con gruppi di cattura
        output_column: Nome colonna output (per pattern senza gruppi nominati).
                       Default: "{column}_extracted"
        extract_all: Se True, estrae tutte le occorrenze e le unisce
                     con virgola (solo per pattern senza gruppi nominati)

    Returns:
        DataFrame con colonne estratte aggiunte
    """
    if column not in df.columns:
        logger.warning(f"StringExtract: colonna '{column}' non trovata")
        return df

    try:
        import re
        # Valida il pattern regex
        try:
            compiled = re.compile(pattern)
        except re.error as regex_err:
            logger.error(f"StringExtract: pattern regex non valido: {regex_err}")
            return df

        result = df.copy()
        text_series = result[column].astype(str)

        # Controlla se ci sono gruppi nominati
        named_groups = compiled.groupindex

        if named_groups:
            # Pattern con gruppi nominati: una colonna per gruppo
            extracted = text_series.str.extract(pattern, expand=True)
            for col_name in extracted.columns:
                result[col_name] = extracted[col_name]
            logger.info(
                f"StringExtract completato: {len(named_groups)} gruppi nominati "
                f"estratti da '{column}'"
            )
        elif extract_all:
            # Estrai tutte le occorrenze, unisci con virgola
            out_col = output_column if output_column else f"{column}_extracted"
            all_matches = text_series.str.findall(pattern)
            result[out_col] = all_matches.apply(
                lambda matches: ",".join(matches) if matches else None
            )
            logger.info(
                f"StringExtract (extractall) completato: output='{out_col}'"
            )
        else:
            # Singola estrazione (primo match, primo gruppo)
            out_col = output_column if output_column else f"{column}_extracted"
            extracted = text_series.str.extract(pattern, expand=True)
            if not extracted.empty:
                result[out_col] = extracted.iloc[:, 0]
            else:
                result[out_col] = None
            logger.info(
                f"StringExtract completato: output='{out_col}'"
            )

        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore StringExtract: {e}", exc_info=True)
        raise


# NORMALIZZAZIONE TESTO
# Operazioni su testo: upper, lower, trim, title, strip accenti.

def op_text_normalize(
    df: DataFrame,
    columns: List[str],
    operations: str = "full",
    overwrite: bool = True
) -> DataFrame:
    """
    Normalizza colonne testo.

    Args:
        df: DataFrame input
        columns: Colonne da normalizzare
        operations: Tipo operazione (trim, lower, upper, title, strip_accents,
                    strip_double_spaces, full)
        overwrite: Se True sovrascrive, altrimenti crea colonna _norm

    Returns:
        DataFrame con colonne normalizzate
    """
    import unicodedata
    import re

    if not columns:
        logger.warning("Text Normalize: nessuna colonna selezionata")
        return df

    valid_columns = [c for c in columns if c in df.columns]
    if not valid_columns:
        logger.warning(f"Text Normalize: nessuna colonna valida tra {columns}")
        return df

    result = df.copy()

    def strip_accents(text):
        """Rimuovi accenti: è→e, à→a, ü→u"""
        if not isinstance(text, str):
            return text
        nfkd = unicodedata.normalize('NFKD', text)
        return ''.join(c for c in nfkd if not unicodedata.combining(c))

    def normalize_text(text, ops):
        """Applica operazioni di normalizzazione."""
        if pd.isna(text) or not isinstance(text, str):
            return text

        s = text

        if ops in ('trim', 'full'):
            s = s.strip()

        if ops == 'lower' or ops == 'full':
            s = s.lower()
        elif ops == 'upper':
            s = s.upper()
        elif ops == 'title':
            s = s.title()

        if ops in ('strip_accents', 'full'):
            s = strip_accents(s)

        if ops in ('strip_double_spaces', 'full'):
            s = re.sub(r'\s+', ' ', s)

        return s

    try:
        for col in valid_columns:
            out_col = col if overwrite else f"{col}_norm"
            result[out_col] = result[col].apply(lambda v: normalize_text(v, operations))

        logger.info(f"Text Normalize ({operations}): {len(valid_columns)} colonne")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Text Normalize: {e}", exc_info=True)
        raise
