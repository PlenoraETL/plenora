"""
Cluster JOINS — operazioni di join tra DataFrame.

Origine
=======
Estratto da `app.core.manipola.transforms.base` come parte di Phase 5
Batch B1 (cluster split, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1). L'estrazione e' splittata
in due commit per rispettare la regola §6.4 sul commit-size:

  - **B1.1a (questo commit)**: introduzione del nuovo modulo `joins.py`
    con `op_join` definita qui. `base.py` resta INVARIATO: il body
    originale di `op_join` e' ancora presente in `base`. Stato
    intermedio: la funzione e' presente in DUE punti (qui + `base`),
    entrambi runtime-equivalenti, importabili indipendentemente.
    Nessun call site cambia.

  - **B1.1b (commit successivo)**: cutover. `base.py` rimuove il body
    di `op_join` e lo sostituisce con `from .joins import op_join`
    (facade re-export). Single-source canonica diventa questo modulo.

Invarianti pubbliche preservate (B1.1a + B1.1b):
  - `from app.core.manipola.transforms.base import op_join` continua a
    funzionare (in B1.1a perche' il body e' ancora la', in B1.1b
    perche' base lo re-importa dal facade).
  - `from app.core.manipola.transforms import op_join` continua a
    funzionare (`__init__.py` invariato).
  - Registry `transforms_registry.json` con entry `"base.op_join"`
    continua a risolvere via `getattr(base_module, "op_join")` —
    in B1.1a perche' op_join e' definita in base, in B1.1b perche'
    re-exported nel namespace di base.
  - Nuovo path canonico `from app.core.manipola.transforms.joins
    import op_join` disponibile da B1.1a in poi.

Il body sotto e' una copia BIT-PER-BIT del body presente in `base.py`
al momento di B1.1a. Nessuna modifica di logica, comment, log payload,
o ordine di operazione: la decomposizione strutturale e' separata dai
fix funzionali per rendere il diff review-friendly e il rollback
chirurgico (vedi regola §6.3 di rollback).
"""

import uuid

import numpy as np
import pandas as pd
import geopandas as gpd
from typing import List, Union

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Stesso alias dtype-union usato in base.py: copre sia pandas DataFrame
# sia geopandas GeoDataFrame (op_join supporta entrambi via copy + merge).
DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# =============================================================================
# JOIN — Unione tabelle per colonna chiave
# Supporta: inner, left, right, outer, cross. Gestisce suffissi per colonne omonime.
# =============================================================================

def op_join(
    inputs: List[DataFrame],
    left_on: str,
    right_on: str,
    how: str = "inner"
) -> DataFrame:
    """
    Join tra due DataFrame.

    Aggiunge suffissi _L e _R a tutte le colonne (tranne chiavi di join)
    per evitare ambiguità.

    Args:
        inputs: Lista di 2 DataFrame [left, right]
        left_on: Colonna chiave del DataFrame sinistro
        right_on: Colonna chiave del DataFrame destro
        how: Tipo join (inner, left, right, outer)

    Returns:
        DataFrame unito
    """
    if len(inputs) < 2:
        logger.warning("Join richiede 2 input, ricevuto meno")
        return inputs[0] if inputs else pd.DataFrame()

    left_df = inputs[0].copy()
    right_df = inputs[1].copy()

    # === PRE-VALIDATION CHIAVE JOIN ===
    # La chiave deve esistere PRIMA di qualsiasi manipolazione (dedup,
    # conversione tipi, rename suffissi). Senza questo, la KeyError
    # arriverebbe post-safe_rename con colonne _L / _R fuorvianti:
    # l'utente vedrebbe nomi suffissati che non esistevano nel config
    # e interpreterebbe il problema come bug del rename, mentre la
    # chiave non era presente nemmeno nel DataFrame originale.
    if left_on not in left_df.columns:
        raise KeyError(
            f"Colonna join sinistra '{left_on}' non esiste nel DataFrame di sinistra. "
            f"Verifica il config del nodo join o lo schema del nodo upstream. "
            f"Colonne disponibili nel left (prime 20): {list(left_df.columns)[:20]}"
        )
    if right_on not in right_df.columns:
        raise KeyError(
            f"Colonna join destra '{right_on}' non esiste nel DataFrame di destra. "
            f"Verifica il config del nodo join o lo schema del nodo upstream. "
            f"Colonne disponibili nel right (prime 20): {list(right_df.columns)[:20]}"
        )

    # === DEDUPLICAZIONE COLONNE ===
    # Se ci sono colonne duplicate, pandas merge fallisce con KeyError.
    # Strategia: se la chiave è duplicata tieni solo la prima occorrenza,
    # per le altre colonne aggiungi suffisso progressivo.
    def dedup_columns(df, key_col):
        seen = {}
        new_cols = []
        key_found = False
        for col in df.columns:
            if col == key_col:
                if not key_found:
                    new_cols.append(col)
                    key_found = True
                else:
                    # Chiave duplicata: rinomina la seconda occorrenza
                    new_cols.append(f"{col}_dup")
                continue
            if col in seen:
                seen[col] += 1
                new_cols.append(f"{col}_{seen[col]}")
            else:
                seen[col] = 0
                new_cols.append(col)
        df.columns = new_cols
        return df

    left_df = dedup_columns(left_df, left_on)
    right_df = dedup_columns(right_df, right_on)

    # === AUTO-CONVERSIONE TIPI CHIAVI DI JOIN ===
    # Se i tipi delle chiavi non corrispondono, prova a convertire.
    if left_on in left_df.columns and right_on in right_df.columns:
        left_dtype = left_df[left_on].dtype
        right_dtype = right_df[right_on].dtype

        if left_dtype != right_dtype:

            # Strategia: converti entrambi a stringa se uno è object, altrimenti a numerico.
            if left_dtype == 'object' or right_dtype == 'object':
                # C1 Phase 4: niente .replace({'nan': None, '<NA>': None, ...}).
                # Il pattern legacy mappava le stringhe 'nan' / '<NA>' / 'None' / ''
                # a None per "ripulire" i missing dopo astype(str). Due bug:
                #  1. una stringa LEGITTIMA "nan" (testo, non missing) veniva
                #     trasformata in None -> esclusa silenziosamente dal join.
                #  2. asimmetria: se la coercion non scattava (stessi dtype),
                #     stringhe "nan" vere collidevano fra di loro come match
                #     fittizi.
                # Fix: cattura la maschera dei missing PRIMA di astype(str) e
                # riapplicala con pd.NA dopo. Solo i veri null tornano null;
                # le stringhe identiche a "nan" / "<NA>" passano invariate.
                def clean_to_str(series):
                    null_mask = series.isna()
                    if pd.api.types.is_numeric_dtype(series):
                        # Numeric -> stringa pulita (1.0 -> "1") via Int64 nullable.
                        try:
                            str_series = series.astype('Int64').astype(str)
                        except (ValueError, TypeError):
                            # Fallback per float non-integer (es. 1.5): astype(str)
                            # diretto, produce "1.5" / "2.0" / "nan".
                            str_series = series.astype(str)
                    else:
                        str_series = series.astype(str).str.strip()
                    # Riapplica la mask: i veri NaN/None/pd.NA tornano pd.NA,
                    # qualunque cosa abbia scritto astype(str) in quelle posizioni
                    # (es. "<NA>" da Int64, "nan" da float, "None" da object con None).
                    return str_series.mask(null_mask, other=pd.NA)

                left_df[left_on] = clean_to_str(left_df[left_on])
                right_df[right_on] = clean_to_str(right_df[right_on])

            else:
                # Converti entrambi a numerico. errors='coerce' produce NaN reali
                # per i valori non parsabili, gestiti correttamente da pandas merge
                # (no NaN==NaN). Niente downstream cleanup serve qui.
                try:
                    left_df[left_on] = pd.to_numeric(left_df[left_on], errors='coerce')
                    right_df[right_on] = pd.to_numeric(right_df[right_on], errors='coerce')
                except Exception as e:
                    logger.warning(f"Join: impossibile convertire a numerico: {e}")

    # Rinomina colonne con suffissi (tranne chiavi join)
    # NOTA: usiamo enumerate per gestire colonne duplicate in modo sicuro
    def safe_rename(df, key_col, suffix):
        cols = list(df.columns)
        new_cols = []
        key_done = False
        for col in cols:
            if col == key_col and not key_done:
                new_cols.append(col)
                key_done = True
            elif col == key_col:
                new_cols.append(f"{col}_{suffix}_dup")
            else:
                new_cols.append(f"{col}_{suffix}")
        df = df.copy()
        df.columns = new_cols
        return df

    left_df = safe_rename(left_df, left_on, 'L')
    right_df = safe_rename(right_df, right_on, 'R')
    logger.info(f"[JOIN] left cols: {list(left_df.columns)[:10]}, left_on={left_on}")
    logger.info(f"[JOIN] right cols: {list(right_df.columns)[:10]}, right_on={right_on}")

    # Garanzia finale: rimuovi eventuali colonne duplicate residue
    left_df = left_df.loc[:, ~left_df.columns.duplicated(keep='first')]
    right_df = right_df.loc[:, ~right_df.columns.duplicated(keep='first')]

    # Guard post-rename [internal]: dopo la pre-validation in testa a
    # op_join, la chiave DEVE esistere qui. Se raggiungiamo questo raise
    # significa che safe_rename ha un bug (ha rinominato/perso la chiave
    # nonostante la pre-validation). Non e' un errore utente, e' una
    # regressione interna: diagnosticarla come tale.
    if left_on not in left_df.columns:
        raise KeyError(
            f"[internal] chiave join sinistra '{left_on}' scomparsa dopo safe_rename "
            f"nonostante pre-validation. Possibile bug interno in op_join.safe_rename. "
            f"Colonne dopo rename: {list(left_df.columns)}"
        )
    if right_on not in right_df.columns:
        raise KeyError(
            f"[internal] chiave join destra '{right_on}' scomparsa dopo safe_rename "
            f"nonostante pre-validation. Possibile bug interno in op_join.safe_rename. "
            f"Colonne dopo rename: {list(right_df.columns)}"
        )

    # === C1 Phase 4: telemetria + sentinel substitution per chiavi NaN ===
    # pandas non considera "NaN==NaN" in linea generale, MA:
    #  - how='outer' con NaN su entrambi i lati: pandas fonde le due righe
    #    NaN in un'unica riga di output (regression vs policy "righe
    #    separate, no cross-NaN match").
    #  - dtype mismatch + roundtrip stringa: i null possono finire come
    #    stringhe "<NA>" / "nan" / "None" e collidere con stringhe
    #    legittime omonime sull'altro lato.
    # Soluzione: ogni NaN viene rimpiazzato con una sentinel univoca per
    # riga e lato. Nessuna sentinel collide con un'altra (left vs right),
    # ne con valori reali (sentinel string casuale). Dopo il merge,
    # ripristiniamo NaN nelle posizioni dei sentinel.
    left_null_mask = left_df[left_on].isna()
    right_null_mask = right_df[right_on].isna()
    left_null_keys = int(left_null_mask.sum())
    right_null_keys = int(right_null_mask.sum())

    if left_null_keys or right_null_keys:
        effetto = (
            "escluse dal risultato" if how == 'inner'
            else "preservate senza match"
        )
        logger.warning(
            f"Join {how}: chiavi NaN — sinistra '{left_on}'={left_null_keys}, "
            f"destra '{right_on}'={right_null_keys}. pandas non matcha "
            f"NaN==NaN, le righe key-NaN saranno {effetto}."
        )

    _join_nan_token = uuid.uuid4().hex[:12]
    _left_sentinel_prefix = f"__JOIN_NULL_L_{_join_nan_token}_"
    _right_sentinel_prefix = f"__JOIN_NULL_R_{_join_nan_token}_"

    if left_null_keys or right_null_keys:
        # Forza object dtype su ENTRAMBE le key (anche quella senza null):
        # se ne casta una sola, pandas merge solleva "trying to merge object
        # and float64 columns". L'object dtype ospita le sentinel string
        # accanto ai valori originali; ripristineremo NaN dopo il merge.
        left_df[left_on] = left_df[left_on].astype(object)
        right_df[right_on] = right_df[right_on].astype(object)
        if left_null_keys:
            left_df.loc[left_null_mask, left_on] = [
                f"{_left_sentinel_prefix}{i}" for i in range(left_null_keys)
            ]
        if right_null_keys:
            right_df.loc[right_null_mask, right_on] = [
                f"{_right_sentinel_prefix}{i}" for i in range(right_null_keys)
            ]

    # Esegui merge
    result = pd.merge(
        left_df, right_df,
        left_on=left_on,
        right_on=right_on,
        how=how,
        suffixes=('', '_dup')
    )

    # Ripristina NaN nelle posizioni dei sentinel sulle key di output.
    if left_null_keys or right_null_keys:
        def _restore_join_nans(series):
            if series.dtype != object:
                return series
            str_view = series.astype(str)
            is_sentinel = (
                str_view.str.startswith(_left_sentinel_prefix, na=False)
                | str_view.str.startswith(_right_sentinel_prefix, na=False)
            )
            return series.mask(is_sentinel, other=np.nan)

        if left_on in result.columns:
            result[left_on] = _restore_join_nans(result[left_on])
        if left_on != right_on and right_on in result.columns:
            result[right_on] = _restore_join_nans(result[right_on])

    # Rimuovi colonna join duplicata se nomi diversi
    if left_on != right_on and right_on in result.columns:
        result = result.drop(columns=[right_on])

    logger.info(f"Join completato: {len(result)} righe, {len(result.columns)} colonne")
    return result
