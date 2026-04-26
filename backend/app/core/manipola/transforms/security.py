"""
Cluster SECURITY — hashing MD5 (change detection / anonimizzazione) +
mascheramento PII fail-closed.

Origine
=======
Estratto da `app.core.manipola.transforms.base` come parte di Phase 5
Batch B1.11 (cluster split + closeout runtime residuals base.py, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1 facade pattern).

Razionale del raggruppamento: tutte le entita' qui dentro condividono
dominio "security/privacy":
  - `op_md5_hash`     — calcolo hash MD5 colonne (change detection +
                        anonimizzazione 1-way).
  - `_mask_cf/_mask_email/_mask_phone/_mask_iban/_mask_custom`
                      — 5 helper privati per pattern di mascheramento
                        PII per tipo (CF, email, telefono, IBAN, custom).
  - `MASK_FUNCTIONS`  — dict registry tipo → funzione, consumato da
                        `op_mask_data` per dispatch.
  - `op_mask_data`    — fail-closed all-or-nothing PII masking
                        (γ1b ratificata, RuntimeError + chain
                        `__cause__` preservata).

Coupling MASK_FUNCTIONS — preservato via patch.dict semantics
=============================================================
Il test contract `tests2/manipola/unit/test_op_mask_data_fail_closed.py`
patcha `MASK_FUNCTIONS['cf']` con:

    patch.dict(
        "app.core.manipola.transforms.base.MASK_FUNCTIONS",
        {"cf": broken_cf_mask},
        clear=False,
    )

`patch.dict` MUTA il dict in-place (NON rebinda l'attributo modulo).
Quindi:
  1. `base.py` re-exporta MASK_FUNCTIONS via `from .security import
     MASK_FUNCTIONS` → `base.MASK_FUNCTIONS is security.MASK_FUNCTIONS`
     (stesso oggetto, identita' Python preservata).
  2. Il patch risolve `base.MASK_FUNCTIONS` → ottiene il dict object →
     muta i valori in place → la mutazione e' visibile da `op_mask_data`
     (qui in security.py) che legge `MASK_FUNCTIONS` via module-level
     name lookup → stesso dict identico.
  3. Il restore al `__exit__` del context manager ripristina i valori
     originali (helper _mask_cf/_mask_email/...).

Matematicamente:
  - `patch.object(base, "MASK_FUNCTIONS", new_dict)` ❌ rebinderebbe
    solo `base.MASK_FUNCTIONS` → propagation rotta verso security
  - `patch.dict("...base.MASK_FUNCTIONS", {...})` ✅ muta dict shared
    → propagation OK

Il test usa `patch.dict`, quindi l'extraction e' safe per costruzione.

Contenuto:
  - `op_md5_hash`        — vectorized MD5 per change detection
                           (~10x speedup vs legacy double-pass agg+map).
  - `_mask_cf`           — RSSMRA80A01H501Z → RSS***********01Z
  - `_mask_email`        — mario.rossi@gmail.com → m****@gmail.com
  - `_mask_phone`        — +39 333 1234567 → +39 ***4567
  - `_mask_iban`         — IT60X0542811101000000123456 →
                           IT60********************3456
  - `_mask_custom`       — N caratteri visibili inizio/fine
  - `MASK_FUNCTIONS`     — dict {tipo: funzione} per dispatch
  - `op_mask_data`       — loop fail-closed all-or-nothing su PII;
                           se fallisce su una colonna → `raise
                           RuntimeError(...)` PRIMA di return result
                           (no leak PII parziale).

Invarianti pubbliche preservate (regola §6.1 zero call-site changes):
  - `from app.core.manipola.transforms.base import op_md5_hash` /
    `op_mask_data` continua a funzionare grazie al facade re-export
    in `base.py`.
  - `from app.core.manipola.transforms import op_md5_hash` /
    `op_mask_data` continua a funzionare (`__init__.py` re-exporta
    entrambi via `from .base import ...` che cattura il facade).
  - `transforms_registry.json` con entries `"base.op_md5_hash"` /
    `"base.op_mask_data"` continua a risolvere via
    `getattr(<base module>, "<func>")` dal `PluginExecutor`.
  - `MASK_FUNCTIONS` re-exported da base.py via `from .security
    import MASK_FUNCTIONS` → identita' oggetto preservata per
    patch.dict (vedi sezione "Coupling" sopra).
  - `_mask_cf/_mask_email/_mask_phone/_mask_iban/_mask_custom` NON
    re-exported da base (verificato: nessun consumer esterno).
    Restano single-source in security.

Logger naming
=============
Logger creato esplicitamente come
`"app.core.manipola.transforms.base"` (NON `__name__`) per preservare
il contratto osservabile pre-extraction: i record `MD5 Hash: ...`,
`[CRASH] Errore MD5 Hash`, `Mask Data: ...`, `[CRASH] Errore
mascheramento colonna ...` continuano ad apparire sotto il logger
name dei consumer. Stesso pattern di tutti i moduli figli estratti
nei batch B1.1-B1.10.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`base.py` al momento di B1.11. Nessuna modifica di logica, comment,
log payload, ordine di operazione, fail-closed all-or-nothing
semantics, vectorized MD5 prep.
"""

import numpy as np
import pandas as pd
import geopandas as gpd
from typing import Any, Dict, List, Union

from app.utils.logger import get_logger

# Stesso logger name di `base.py`: i record sono indistinguibili dai
# consumer che attaccano handler / filtrano per
# "app.core.manipola.transforms.base" (pattern §6.1 facade).
logger = get_logger("app.core.manipola.transforms.base")

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# =============================================================================
# HASH MD5 (Rilevamento modifiche)
# Calcola hash MD5 di colonne selezionate per change detection e anonimizzazione.
# =============================================================================

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
    import hashlib

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
        # Per-column prep vettoriale (pandas-native, non e' l'hotspot).
        # Hotspot storico: il doppio passaggio .agg(join, axis=1) + .map(md5)
        # — due row-iter Python consecutivi. Qui li fondiamo in un singolo
        # list-comp su numpy arrays: zip() itera in C, separator.join/
        # hashlib.md5/encode sono tutti C-impl nativi. Speedup tipico ~10x
        # su 10k righe vs il legacy "agg+map".
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


# =============================================================================
# MASCHERAMENTO DATI SENSIBILI
# Nasconde dati sensibili con pattern (es: Mario Rossi → M**** R*****).
# =============================================================================

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
    import re
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
#
# IMPORTANTE: questo dict viene re-exported da `base.py` via
# `from .security import MASK_FUNCTIONS`. Il test contract
# `tests2/manipola/unit/test_op_mask_data_fail_closed.py:69,110`
# patcha `"app.core.manipola.transforms.base.MASK_FUNCTIONS"` con
# `patch.dict` (NON `patch.object`). `patch.dict` muta il dict
# in-place; poiche' `base.MASK_FUNCTIONS is security.MASK_FUNCTIONS`
# (stesso oggetto), la mutazione e' visibile da `op_mask_data` qui
# sotto che legge `MASK_FUNCTIONS` via module-level name lookup.
# NON convertire in `MASK_FUNCTIONS = MappingProxyType({...})` o
# rebindare a un nuovo dict in qualunque punto della catena: il
# patch contract dipende dall'identita' oggetto preservata.
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

        # intent: fail-closed all-or-nothing masking — privacy-safe.
        # Se una colonna fallisce, l'intero transform fallisce prima di
        # ritornare un `result` con colonne parzialmente mascherate (leak
        # PII). Il caller executor manipola (batch 5 E1 chiuso) cattura
        # RuntimeError e traduce in error dict del nodo. str(e) tecnico
        # preservato nel logger.error [CRASH] server-side per postmortem.
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
