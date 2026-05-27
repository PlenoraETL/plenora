"""
Cluster SECURITY — hashing MD5 (change detection / anonimizzazione) +

mascheramento PII fail-closed.
"""

import hashlib
import re

import numpy as np
from typing import Any, List

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.tabular import empty_object_series
from app.core.manipola.types import JsonRows
from app.utils.logger import get_logger

logger = get_logger(__name__)

DataFrame = Any

# MANIPOLA-3: dimensione del batch per md5 hash RAM-bounded.
# 50k righe x 8 colonne x ~100 byte avg = ~40MB di peak per batch
# (vs unbounded per intero DataFrame pre-fix).
_HASH_BATCH_SIZE = 50_000


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

    result = copy_frame_for_update(df)
    separator = "\x1f"  # Unit Separator — non appare nei dati reali

    try:
        # MANIPOLA-3 (2026-05-26): batch RAM-bounded.
        # Pre-fix: materializzava `values + buf + np.array` per ognuna delle
        # N colonne contemporaneamente (peak ~ N_cols * N_rows * avg_str_size).
        # Su 5M righe x 8 colonne con stringhe ~50 byte: ~2GB di peak.
        # Post-fix: processa batch da `_HASH_BATCH_SIZE` (50k righe) per
        # volta. Peak ~ N_cols * 50k * avg_str_size ~ poche centinaia di MB
        # max, indipendente da N_rows.
        #
        # Hot-path stabilizzato: lavoro su np.ndarray + Python puro per
        # bypassare il path pandas/pyarrow di `s.map(lambda)` /
        # `.astype(str)`, riprodottosi instabile (SIGFPE/SIGSEGV
        # intermittente sotto pytest in presenza di pandas+pyarrow+shapely
        # caricati, ~30-70% dei run del bucket performance). La forma a
        # list-comp ha la stessa semantica osservabile (NaN→"", Wkt per
        # geometrie shapely, str altrove) ma evita il dispatch interno
        # di pandas verso le extension che hanno l'interaction bug.
        n = len(result)
        if n == 0:
            result[output_column] = empty_object_series()
            logger.info(f"MD5 Hash: {len(valid_columns)} colonne -> {output_column}")
            return result

        md5 = hashlib.md5
        out = np.empty(n, dtype=object)

        for batch_start in range(0, n, _HASH_BATCH_SIZE):
            batch_end = min(batch_start + _HASH_BATCH_SIZE, n)
            batch_size = batch_end - batch_start

            # Per ogni colonna prepara solo i bytes del batch.
            col_arrs_batch: List[np.ndarray] = []
            for col in valid_columns:
                values = result[col].iloc[batch_start:batch_end].tolist()
                buf = [""] * batch_size
                for i in range(batch_size):
                    v = values[i]
                    if v is None:
                        continue
                    try:
                        if v != v:  # NaN/NaT idiom
                            continue
                    except TypeError:
                        pass
                    if hasattr(v, "wkt"):
                        buf[i] = v.wkt
                    else:
                        buf[i] = str(v)
                if normalize:
                    for i in range(batch_size):
                        buf[i] = buf[i].strip().lower()
                col_arrs_batch.append(np.array(buf, dtype=object))

            # Hash del batch, scrivi nell'output finale.
            for j, row in enumerate(zip(*col_arrs_batch)):
                out[batch_start + j] = md5(
                    separator.join(row).encode("utf-8")
                ).hexdigest()

            # Free batch arrays prima del prossimo iter (riduzione esplicita
            # del peak — GC fara' la stessa cosa appena out-of-scope).
            del col_arrs_batch

        result[output_column] = out
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
# NOTE: questo dict deve restare mutabile e patchabile in-place:
# `op_mask_data` legge `MASK_FUNCTIONS` via module-level name lookup e i
# contract test lo sostituiscono con `patch.dict`.
# NON convertire in `MASK_FUNCTIONS = MappingProxyType({...})` o rebindare
# a un nuovo dict.
MASK_FUNCTIONS = {
    'cf': _mask_cf,
    'email': _mask_email,
    'phone': _mask_phone,
    'iban': _mask_iban,
}


def op_mask_data(
    df: DataFrame,
    maskings: JsonRows,
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

    result = copy_frame_for_update(df)

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
