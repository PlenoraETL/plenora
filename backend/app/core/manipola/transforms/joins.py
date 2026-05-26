"""
Cluster JOINS — operazioni di join tra DataFrame.

Keeps transformation behavior isolated from orchestration callers.
"""

import uuid

import numpy as np
from typing import List

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.ports import FrameLike
from app.core.dataframes.tabular import (
    coerce_numeric_series,
    concat_dataframes,
    drop_duplicate_columns,
    empty_dataframe,
    is_numeric_series,
    merge_dataframes,
    missing_scalar,
    set_values_where_mask,
)
from app.core.resources.exceptions import MemoryLimitExceededError
from app.utils.logger import get_logger

logger = get_logger("app.core.manipola.transforms.base")

DataFrame = FrameLike


# JOIN — Unione tabelle per colonna chiave
# Supporta: inner, left, right, outer, cross. Gestisce suffissi per colonne omonime

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
        return inputs[0] if inputs else empty_dataframe()

    left_df = copy_frame_for_update(inputs[0])
    right_df = copy_frame_for_update(inputs[1])

    # La chiave deve esistere PRIMA di qualsiasi manipolazione (dedup
    # conversione tipi, rename suffissi). Senza questo, la KeyError
    # arriverebbe post-safe_rename con colonne _L / _R fuorvianti
    # l'utente vedrebbe nomi suffissati che non esistevano nel config
    # e interpreterebbe il problema come bug del rename, mentre la
    # chiave non era presente nemmeno nel DataFrame originale
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

    # Se ci sono colonne duplicate, pandas merge fallisce con KeyError
    # Strategia: se la chiave è duplicata tieni solo la prima occorrenza
    # per le altre colonne aggiungi suffisso progressivo
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

    # Se i tipi delle chiavi non corrispondono, prova a convertire
    if left_on in left_df.columns and right_on in right_df.columns:
        left_dtype = left_df[left_on].dtype
        right_dtype = right_df[right_on].dtype

        if left_dtype != right_dtype:

            # Strategia: converti entrambi a stringa se uno è object, altrimenti a numerico
            if left_dtype == 'object' or right_dtype == 'object':
                def clean_to_str(series):
                    null_mask = series.isna()
                    if is_numeric_series(series):
                        # Numeric -> stringa pulita (1.0 -> "1") via Int64 nullable
                        try:
                            str_series = series.astype('Int64').astype(str)
                        except (ValueError, TypeError):
                            # Fallback per float non-integer (es. 1.5): astype(str
                            # diretto, produce "1.5" / "2.0" / "nan"
                            str_series = series.astype(str)
                    else:
                        str_series = series.astype(str).str.strip()
                    # Riapplica la mask: i veri NaN/None/pd.NA tornano pd.NA
                    # qualunque cosa abbia scritto astype(str) in quelle posizioni
                    # es. "<NA>" da Int64, "nan" da float, "None" da object con None
                    return str_series.mask(null_mask, other=missing_scalar())

                left_df[left_on] = clean_to_str(left_df[left_on])
                right_df[right_on] = clean_to_str(right_df[right_on])

            else:
                # Converti entrambi a numerico. errors='coerce' produce NaN reali
                # per i valori non parsabili, gestiti correttamente da pandas merge
                # no NaN==NaN). Niente downstream cleanup serve qui
                try:
                    left_df[left_on] = coerce_numeric_series(left_df[left_on])
                    right_df[right_on] = coerce_numeric_series(right_df[right_on])
                except Exception as e:
                    logger.warning(f"Join: impossibile convertire a numerico: {e}")

    # Rinomina colonne con suffissi (tranne chiavi join
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
        df = copy_frame_for_update(df)
        df.columns = new_cols
        return df

    left_df = safe_rename(left_df, left_on, 'L')
    right_df = safe_rename(right_df, right_on, 'R')
    logger.info(f"[JOIN] left cols: {list(left_df.columns)[:10]}, left_on={left_on}")
    logger.info(f"[JOIN] right cols: {list(right_df.columns)[:10]}, right_on={right_on}")

    # Garanzia finale: rimuovi eventuali colonne duplicate residue
    left_df = drop_duplicate_columns(left_df, keep='first')
    right_df = drop_duplicate_columns(right_df, keep='first')

    # Guard post-rename [internal]: dopo la pre-validation in testa a
    # op_join, la chiave DEVE esistere qui. Se raggiungiamo questo raise
    # significa che safe_rename ha un bug (ha rinominato/perso la chiave
    # nonostante la pre-validation). Non e' un errore utente, e' una
    # regressione interna: diagnosticarla come tale
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

    # pandas non considera "NaN==NaN" in linea generale, MA
    # how='outer' con NaN su entrambi i lati: pandas fonde le due righe
    # NaN in un'unica riga di output (regression vs policy "righe
    # separate, no cross-NaN match"
    # dtype mismatch + roundtrip stringa: i null possono finire come
    # stringhe "<NA>" / "nan" / "None" e collidere con stringhe
    # legittime omonime sull'altro lato
    # Soluzione: ogni NaN viene rimpiazzato con una sentinel univoca per
    # riga e lato. Nessuna sentinel collide con un'altra (left vs right
    # ne con valori reali (sentinel string casuale). Dopo il merge
    # ripristiniamo NaN nelle posizioni dei sentinel
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

    # MANIPOLA-6 (2026-05-26): cardinality guard many-to-many.
    # Pre-fix: un join `inner` con 50k righe x 50k righe su chiave a
    # cardinalita' alta (es. solo 10 valori distinti) produceva 50k * 50k
    # / 10 = 250M righe — OOM immediato. Mirror del guard di cross_join.
    # Post-fix: stima cap pre-merge con bound peggiore basato sulle
    # frequenze di chiave; se supera la soglia, fail-closed con messaggio
    # operativo invece di OOM silente.
    _MAX_JOIN_OUTPUT_ROWS = 50_000_000  # 50M righe = ~5-10GB peak tipico
    try:
        left_keys = left_df[left_on]
        right_keys = right_df[right_on]
        # Bound peggiore: per ogni valore chiave, sum(left_count * right_count).
        # Usa value_counts (cheap) invece di un vero groupby cross-product.
        left_vc = left_keys.value_counts(dropna=False)
        right_vc = right_keys.value_counts(dropna=False)
        # Common keys: prodotto delle frequenze sommate.
        common = left_vc.index.intersection(right_vc.index)
        if len(common) > 0:
            estimated_rows = int(
                (left_vc.loc[common] * right_vc.loc[common]).sum()
            )
            if how in ("left", "outer"):
                # Aggiungi le righe left senza match (preservate).
                estimated_rows += int(left_vc.drop(common).sum())
            if how in ("right", "outer"):
                estimated_rows += int(right_vc.drop(common).sum())
            if estimated_rows > _MAX_JOIN_OUTPUT_ROWS:
                raise ValueError(
                    f"Join {how} su chiavi ('{left_on}' x '{right_on}') "
                    f"produrrebbe ~{estimated_rows:,} righe (cap "
                    f"{_MAX_JOIN_OUTPUT_ROWS:,}). Probabile many-to-many "
                    f"con chiavi a bassa cardinalita'. Verifica le chiavi "
                    f"o riduci le tabelle in input."
                )
    except ValueError:
        raise
    except Exception as exc:
        # value_counts su tipi esotici puo' fallire: log e prosegui (non
        # blocco l'operazione per un guard fallito).
        logger.debug(f"Join cardinality guard skipped: {exc}")

    _join_nan_token = uuid.uuid4().hex[:12]
    _left_sentinel_prefix = f"__JOIN_NULL_L_{_join_nan_token}_"
    _right_sentinel_prefix = f"__JOIN_NULL_R_{_join_nan_token}_"

    if left_null_keys or right_null_keys:
        # Forza object dtype su ENTRAMBE le key (anche quella senza null
        # se ne casta una sola, pandas merge solleva "trying to merge object
        # and float64 columns". L'object dtype ospita le sentinel string
        # accanto ai valori originali; ripristineremo NaN dopo il merge
        left_df[left_on] = left_df[left_on].astype(object)
        right_df[right_on] = right_df[right_on].astype(object)
        if left_null_keys:
            set_values_where_mask(
                left_df,
                left_null_mask,
                left_on,
                [f"{_left_sentinel_prefix}{i}" for i in range(left_null_keys)],
            )
        if right_null_keys:
            set_values_where_mask(
                right_df,
                right_null_mask,
                right_on,
                [f"{_right_sentinel_prefix}{i}" for i in range(right_null_keys)],
            )

    # Esegui merge
    result = merge_dataframes(
        left_df, right_df,
        left_on=left_on,
        right_on=right_on,
        how=how,
        suffixes=('', '_dup')
    )

    # Ripristina NaN nelle posizioni dei sentinel sulle key di output
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


# UNIONE / CONCATENAZIONE VERTICALE
# Impila righe di più DataFrame. Allinea automaticamente colonne per nome

def op_union(
    inputs: List[DataFrame],
    ignore_index: bool = True
) -> DataFrame:
    """
    Concatena più DataFrame verticalmente (append righe).

    Args:
        inputs: Lista di DataFrame da concatenare
        ignore_index: Se True, resetta gli indici

    Returns:
        DataFrame concatenato
    """
    if not inputs:
        return empty_dataframe()

    if len(inputs) == 1:
        return inputs[0]

    result = concat_dataframes(inputs, ignore_index=ignore_index)
    logger.info(f"Union completato: {len(result)} righe da {len(inputs)} sorgenti")
    return result


# CROSS JOIN — Prodotto cartesiano di due DataFrame
# Genera tutte le combinazioni possibili di righe tra due tabelle

CROSS_JOIN_STAGE = "manipola_cross_join"


def op_cross_join(
    dfs: List[DataFrame],
    **kwargs
) -> DataFrame:
    """
    Prodotto cartesiano (cross join) di due DataFrame.

    Args:
        dfs: Lista di 2 DataFrame [left, right]

    Returns:
        DataFrame con tutte le combinazioni di righe
    """
    if not dfs:
        return empty_dataframe()
    if len(dfs) < 2:
        logger.warning("CrossJoin richiede 2 input, ricevuto meno")
        return dfs[0]

    left_df = dfs[0]
    right_df = dfs[1]

    # Cross-join produce |left| * |right| righe. Inputs anche modesti
    # 10k x 10k = 100M righe) generano un DataFrame che certamente
    # sfora la memoria del worker (1.5 GB cap RSS, vedi
    # WORKER_RSS_MAX_BYTES). Stimiamo l'output PRIMA di
    # materializzare via pd.merge: lookup costante O(1) (gia'
    # disponibile via len), zero costo IO/memoria aggiuntivo
    # Reuse del contratto Memory/OOM
    # eccezione PURA condivisa `MemoryLimitExceededError`
    # codice envelope `memory_limit_exceeded`
    # status 413 (Payload Too Large: gli input sono troppo grandi
    # per un cross-join sostenibile
    # stage `manipola_cross_join` distingue il caller dal file
    # preflight e dal worker RSS sampling
    # Cap configurabile via `CROSS_JOIN_MAX_OUTPUT_ROWS` (settings
    # Cap <= 0 disabilita il guard (escape hatch testing/admin
    try:
        from app.config import settings as _settings
        _cap_rows = int(_settings.get("CROSS_JOIN_MAX_OUTPUT_ROWS", 50_000_000))
    except Exception:
        # Contesto pure-test senza Settings inizializzato: guard off
        _cap_rows = 0

    if _cap_rows > 0:
        _output_rows = len(left_df) * len(right_df)
        if _output_rows > _cap_rows:
            logger.warning(
                f"CrossJoin preflight: output stimato {_output_rows} righe "
                f"({len(left_df)} x {len(right_df)}) supera il cap configurato "
                f"di {_cap_rows} righe — guard attivato."
            )
            raise MemoryLimitExceededError(
                observed_bytes=_output_rows,
                limit_bytes=_cap_rows,
                path_basename="cross_join_output",
                stage=CROSS_JOIN_STAGE,
            )

    try:
        result = merge_dataframes(left_df, right_df, how="cross")

        logger.info(
            f"CrossJoin completato: {len(left_df)} x {len(right_df)} = {len(result)} righe"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore CrossJoin: {e}", exc_info=True)
        raise
