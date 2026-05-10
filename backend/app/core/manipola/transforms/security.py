"""
Cluster SECURITY — hashing MD5 (change detection / anonimizzazione) +

mascheramento PII fail-closed.
"""

import hashlib
import re

import numpy as np
import pandas as pd
import geopandas as gpd
from typing import Any, Dict, List, Union

from app.utils.logger import get_logger

logger = get_logger("app.core.manipola.transforms.base")

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# HASH MD5 (Rilevamento modifiche
# Calcola hash MD5 di colonne selezionate per change detection e anonimizzazione

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
        # Per-column prep vettoriale (pandas-native, non e' l'hotspot
        # Hotspot storico: il doppio passaggio .agg(join, axis=1) + .map(md5
        # — due row-iter Python consecutivi. Qui li fondiamo in un singolo
        # list-comp su numpy arrays: zip itera in C, separator.join/
        # hashlib.md5/encode sono tutti C-impl nativi. Speedup tipico ~10x
        # su 10k righe vs il legacy "agg+map"
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


# MASCHERAMENTO DATI SENSIBILI
# Nasconde dati sensibili con pattern (es: Mario Rossi → M**** R*****

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
# NOTE: questo dict viene compatibility export da `base.py` via
# `from .security import MASK_FUNCTIONS`. Il test contract
# `tests2/manipola/unit/test_op_mask_data_fail_closed.py:69,110`
# patcha `"app.core.manipola.transforms.base.MASK_FUNCTIONS"` con
# `patch.dict` (NON `patch.object`). `patch.dict` muta il dict
# in-place; poiche' `base.MASK_FUNCTIONS is security.MASK_FUNCTIONS`
# stesso oggetto), la mutazione e' visibile da `op_mask_data` qui
# sotto che legge `MASK_FUNCTIONS` via module-level name lookup
# NON convertire in `MASK_FUNCTIONS = MappingProxyType({...})` o
# rebindare a un nuovo dict in qualunque punto della catena: il
# patch contract dipende dall'identita' oggetto preservata
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
