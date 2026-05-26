"""
Cluster STRINGS — operazioni di utilita' su colonne stringa.

Keeps transformation behavior isolated from orchestration callers.
"""

import re
import unicodedata

from typing import Any, List

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.ports import FrameLike
from app.core.dataframes.tabular import dataframe_first_column
from app.core.dataframes.values import is_null_scalar
from app.utils.logger import get_logger

logger = get_logger("app.core.manipola.transforms.base")


# STRING PAD — Riempimento stringa a larghezza fissa
# Aggiunge caratteri di riempimento a sinistra o a destra

def op_string_pad(
    df: FrameLike,
    column: str,
    width: int = 5,
    side: str = "left",
    fill_char: str = "0",
    output_column: str = None
) -> FrameLike:
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
        result = copy_frame_for_update(df)
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
# Aggiunge una colonna con la lunghezza di ogni valore

def op_string_length(
    df: FrameLike,
    column: str,
    output_column: str = None
) -> FrameLike:
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
        result = copy_frame_for_update(df)
        out_col = output_column if output_column else f"{column}_length"

        result[out_col] = result[column].astype(str).str.len()

        logger.info(f"StringLength completato: '{column}' -> '{out_col}'")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore StringLength: {e}", exc_info=True)
        raise


# STRING EXTRACT — StringSearcher
# Estrae testo da una colonna tramite espressione regolare

def op_string_extract(
    df: FrameLike,
    column: str,
    pattern: str,
    output_column: str = None,
    extract_all: bool = False
) -> FrameLike:
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
        # Valida il pattern regex
        try:
            compiled = re.compile(pattern)
        except re.error as regex_err:
            logger.error(f"StringExtract: pattern regex non valido: {regex_err}")
            return df

        result = copy_frame_for_update(df)
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
            # Singola estrazione (primo match, primo gruppo
            out_col = output_column if output_column else f"{column}_extracted"
            extracted = text_series.str.extract(pattern, expand=True)
            if not extracted.empty:
                result[out_col] = dataframe_first_column(extracted)
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
# Operazioni su testo: upper, lower, trim, title, strip accenti

def op_text_normalize(
    df: FrameLike,
    columns: List[str],
    operations: str = "full",
    overwrite: bool = True
) -> FrameLike:
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

    if not columns:
        logger.warning("Text Normalize: nessuna colonna selezionata")
        return df

    valid_columns = [c for c in columns if c in df.columns]
    if not valid_columns:
        logger.warning(f"Text Normalize: nessuna colonna valida tra {columns}")
        return df

    result = copy_frame_for_update(df)

    def strip_accents(text: Any) -> Any:
        """Rimuovi accenti: è→e, à→a, ü→u"""
        if not isinstance(text, str):
            return text
        nfkd = unicodedata.normalize('NFKD', text)
        return ''.join(c for c in nfkd if not unicodedata.combining(c))

    def normalize_text(text: Any, ops: str) -> Any:
        """Applica operazioni di normalizzazione."""
        if is_null_scalar(text) or not isinstance(text, str):
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

    # MANIPOLA-5 (2026-05-26): branch vettoriale per operazioni `.str.*`
    # native pandas. ~10x piu' veloce + peak RAM ridotto vs `.apply(lambda)`
    # Python row-iter. Solo `strip_accents` (unicodedata combining) richiede
    # ancora `.apply()` perche' non c'e' equivalente pandas vettoriale.
    #
    # M-5 follow-up: il branch vettoriale applica `.str.*` SOLO al
    # sub-set di valori string del Series, preservando inalterati i
    # non-string (int, float, bool, ecc.). Pre-fix-fix: `.str.lower()`
    # su una Series object mista produceva NaN sui non-string,
    # rompendo la semantica originale `normalize_text(text)` che
    # ritornava `text` se non era str. Su Series numerica `.str.*`
    # solleva — qui invece resta no-op perche' la mask di string e'
    # vuota.
    needs_strip_accents = operations in ("strip_accents", "full")

    def _apply_vectorial_str_subset(s):
        """Applica le ops `.str.*` solo ai valori string del Series,
        copiando inalterati i non-string. Preserva la semantica del
        legacy `normalize_text` su pandas Series con tipi misti."""
        import pandas as _pd

        # Mask delle posizioni che sono effettivamente stringhe (no None,
        # no NaN, no int/float/bool/date/altre cose). Su una Series
        # numerica pura -> mask vuota -> output identico all'input.
        is_str_mask = s.map(lambda v: isinstance(v, str))
        if not is_str_mask.any():
            # Niente da fare: tutti non-string. Ritorna il Series invariato
            # (copia per safety, evita aliasing con result[col]).
            return s.copy()

        str_subset = s[is_str_mask].astype("string")

        if operations in ("trim", "full"):
            str_subset = str_subset.str.strip()
        if operations == "lower" or operations == "full":
            str_subset = str_subset.str.lower()
        elif operations == "upper":
            str_subset = str_subset.str.upper()
        elif operations == "title":
            str_subset = str_subset.str.title()
        if operations in ("strip_double_spaces", "full"):
            str_subset = str_subset.str.replace(r"\s+", " ", regex=True)

        # Riassembla: copia del Series originale + assign delle posizioni
        # string trasformate. I non-string restano invariati.
        out = s.copy()
        # Converti l'output string-extension a Python str native (per
        # equivalenza dtype/repr con il legacy apply).
        out.loc[is_str_mask] = [
            v if _pd.isna(v) else str(v) for v in str_subset
        ]
        return out

    try:
        for col in valid_columns:
            out_col = col if overwrite else f"{col}_norm"
            source = result[col]
            if needs_strip_accents:
                # strip_accents richiede unicodedata: fallback `.apply()`.
                result[out_col] = source.apply(
                    lambda v: normalize_text(v, operations)
                )
            else:
                # Vettoriale `.str.*` pandas su SOLO i valori string:
                # ~10x piu' veloce + peak ridotto sulle ops vettoriali;
                # preserva non-string come il legacy `normalize_text`.
                result[out_col] = _apply_vectorial_str_subset(source)

        logger.info(f"Text Normalize ({operations}): {len(valid_columns)} colonne")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Text Normalize: {e}", exc_info=True)
        raise
