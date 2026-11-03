"""
Cluster JOINS — operazioni di join tra DataFrame.

Keeps transformation behavior isolated from orchestration callers.
"""

import math
import uuid
from typing import Any, List

import numpy as np

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.ports import FrameLike
from app.core.manipola.join_memory import (
    CROSS_JOIN_MEMORY_STAGE,
    JOIN_MEMORY_STAGE,
    guard_estimated_concat_memory,
    guard_estimated_merge_memory,
    plan_merge_partitions,
)
from app.core.dataframes.tabular import (
    coerce_numeric_series,
    concat_dataframes,
    drop_duplicate_columns,
    empty_dataframe,
    is_numeric_series,
    is_string_like_series,
    merge_dataframes,
    missing_scalar,
    set_values_where_mask,
)
from app.core.resources.exceptions import MemoryLimitExceededError
from app.utils.logger import get_logger
from app.shared.execution.column_selection import join_column_plan

logger = get_logger(__name__)

DataFrame = FrameLike

_NULL_PARTITION_MIN_ROWS = 10_000
_HASH_PARTITION_MIN_ROWS = 100_000


def _normalize_join_keys(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _estimate_join_output_rows_by_keys(
    left: DataFrame,
    right: DataFrame,
    left_keys: List[str],
    right_keys: List[str],
    how: str,
) -> int | None:
    join_how = str(how or "inner").lower()
    if join_how not in {"inner", "left", "right", "outer"}:
        return None
    if not left_keys or len(left_keys) != len(right_keys):
        return None

    # `dropna=True` esclude direttamente i gruppi con almeno una join key
    # nulla, evitando sia il filtro booleano dei frame sia la conversione dei
    # conteggi in dict/tuple Python (costosa su chiavi ad alta cardinalita').
    left_group_key = left_keys[0] if len(left_keys) == 1 else left_keys
    right_group_key = right_keys[0] if len(right_keys) == 1 else right_keys
    left_counts = left.groupby(left_group_key, dropna=True).size()
    right_counts = right.groupby(right_group_key, dropna=True).size()

    # I nomi delle chiavi possono differire fra i due lati. Normalizzare i
    # nomi dell'indice permette intersection/reindex per valore senza creare
    # copie dei DataFrame sorgente.
    if len(left_keys) == 1:
        left_counts.index = left_counts.index.rename(None)
        right_counts.index = right_counts.index.rename(None)
    else:
        unnamed_levels = [None] * len(left_keys)
        left_counts.index = left_counts.index.set_names(unnamed_levels)
        right_counts.index = right_counts.index.set_names(unnamed_levels)

    matched_keys = left_counts.index.intersection(right_counts.index)
    if len(matched_keys):
        left_matched = left_counts.reindex(matched_keys).to_numpy(
            dtype=np.int64,
            copy=False,
        )
        right_matched = right_counts.reindex(matched_keys).to_numpy(
            dtype=np.int64,
            copy=False,
        )
        matched_rows = int(np.dot(left_matched, right_matched))
        left_matched_input_rows = int(left_matched.sum())
        right_matched_input_rows = int(right_matched.sum())
    else:
        matched_rows = 0
        left_matched_input_rows = 0
        right_matched_input_rows = 0

    left_unmatched_rows = len(left) - left_matched_input_rows
    right_unmatched_rows = len(right) - right_matched_input_rows

    if join_how == "inner":
        return int(matched_rows)
    if join_how == "left":
        return int(matched_rows + left_unmatched_rows)
    if join_how == "right":
        return int(matched_rows + right_unmatched_rows)
    return int(matched_rows + left_unmatched_rows + right_unmatched_rows)


# JOIN — Unione tabelle per colonna chiave
# Supporta: inner, left, right, outer, cross. Gestisce suffissi per colonne omonime

def op_join(
    inputs: List[DataFrame],
    left_on: Any = None,
    right_on: Any = None,
    how: str = "inner",
    left_keys: Any = None,
    right_keys: Any = None,
    left_columns: list[str] | None = None,
    right_columns: list[str] | None = None,
    name_conflict: str | None = None,
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
    left_key_cols = _normalize_join_keys(left_keys if left_keys is not None else left_on)
    right_key_cols = _normalize_join_keys(right_keys if right_keys is not None else right_on)

    # Il percorso legacy resta identico quando i nuovi parametri sono assenti.
    column_maps = None
    if left_columns is not None or right_columns is not None or name_conflict is not None:
        column_maps = join_column_plan(
            list(left_df.columns), list(right_df.columns), left_key_cols, right_key_cols,
            left_columns, right_columns, name_conflict,
        )
        left_df = left_df[list(column_maps[0])]
        right_df = right_df[list(column_maps[1])]

    if not left_key_cols or not right_key_cols:
        raise ValueError("Join richiede almeno una coppia di chiavi SX/DX.")
    if len(left_key_cols) != len(right_key_cols):
        raise ValueError(
            "Join con chiavi multiple non valido: il numero di chiavi sinistre "
            f"({len(left_key_cols)}) deve coincidere con quello delle chiavi destre "
            f"({len(right_key_cols)})."
        )

    # La chiave deve esistere PRIMA di qualsiasi manipolazione (dedup
    # conversione tipi, rename suffissi). Senza questo, la KeyError
    # arriverebbe post-safe_rename con colonne _L / _R fuorvianti
    # l'utente vedrebbe nomi suffissati che non esistevano nel config
    # e interpreterebbe il problema come bug del rename, mentre la
    # chiave non era presente nemmeno nel DataFrame originale
    missing_left = [key for key in left_key_cols if key not in left_df.columns]
    if missing_left:
        raise KeyError(
            f"Colonne join sinistre mancanti {missing_left}. "
            f"Verifica il config del nodo join o lo schema del nodo upstream. "
            f"Colonne disponibili nel left (prime 20): {list(left_df.columns)[:20]}"
        )
    missing_right = [key for key in right_key_cols if key not in right_df.columns]
    if missing_right:
        raise KeyError(
            f"Colonne join destre mancanti {missing_right}. "
            f"Verifica il config del nodo join o lo schema del nodo upstream. "
            f"Colonne disponibili nel right (prime 20): {list(right_df.columns)[:20]}"
        )

    # Se ci sono colonne duplicate, pandas merge fallisce con KeyError
    # Strategia: se la chiave è duplicata tieni solo la prima occorrenza
    # per le altre colonne aggiungi suffisso progressivo
    def dedup_columns(df, key_cols):
        key_set = set(key_cols)
        seen = {}
        new_cols = []
        key_seen = {}
        for col in df.columns:
            if col in key_set:
                key_seen[col] = key_seen.get(col, 0) + 1
                if key_seen[col] == 1:
                    new_cols.append(col)
                else:
                    # Chiave duplicata: rinomina la seconda occorrenza
                    new_cols.append(f"{col}_dup{key_seen[col]}")
                continue
            if col in seen:
                seen[col] += 1
                new_cols.append(f"{col}_{seen[col]}")
            else:
                seen[col] = 0
                new_cols.append(col)
        df.columns = new_cols
        return df

    left_df = dedup_columns(left_df, left_key_cols)
    right_df = dedup_columns(right_df, right_key_cols)

    # Se i tipi delle chiavi non corrispondono, prova a convertire
    for left_key, right_key in zip(left_key_cols, right_key_cols):
        left_dtype = left_df[left_key].dtype
        right_dtype = right_df[right_key].dtype

        if left_dtype != right_dtype:

            # Strategia: se ALMENO un lato e' string-like converti entrambi
            # a stringa; il ramo numerico resta solo per coppie davvero
            # numeriche. Il vecchio check `== 'object'` era corretto con
            # pandas 2 (stringhe sempre object): con pandas 3 due varianti
            # stringa diverse (es. 'str' arrow da parquet vs 'string' da
            # groupby) non sono 'object' e cadevano nel ramo numerico, dove
            # to_numeric(errors='coerce') azzerava a NaN tutte le chiavi
            # alfanumeriche su ENTRAMBI i lati: join "riuscito" con zero
            # match e chiavi svuotate, senza alcun errore visibile.
            if (
                is_string_like_series(left_df[left_key])
                or is_string_like_series(right_df[right_key])
            ):
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

                left_df[left_key] = clean_to_str(left_df[left_key])
                right_df[right_key] = clean_to_str(right_df[right_key])

            else:
                # Converti entrambi a numerico. errors='coerce' produce NaN reali
                # per i valori non parsabili, gestiti correttamente da pandas merge
                # no NaN==NaN). Niente downstream cleanup serve qui
                try:
                    left_df[left_key] = coerce_numeric_series(left_df[left_key])
                    right_df[right_key] = coerce_numeric_series(right_df[right_key])
                except Exception as e:
                    logger.warning(f"Join: impossibile convertire a numerico: {e}")

    # Rinomina colonne con suffissi (tranne chiavi join
    # NOTA: usiamo enumerate per gestire colonne duplicate in modo sicuro
    def safe_rename(df, key_cols, suffix):
        key_set = set(key_cols)
        cols = list(df.columns)
        new_cols = []
        key_done = {}
        for col in cols:
            if col in key_set:
                key_done[col] = key_done.get(col, 0) + 1
                if key_done[col] == 1:
                    new_cols.append(col)
                else:
                    new_cols.append(f"{col}_{suffix}_dup{key_done[col]}")
            else:
                new_cols.append(f"{col}_{suffix}")
        df.columns = new_cols
        return df

    if column_maps is None:
        left_df = safe_rename(left_df, left_key_cols, 'L')
        right_df = safe_rename(right_df, right_key_cols, 'R')
    else:
        left_df = left_df.rename(columns=column_maps[0])
        right_df = right_df.rename(columns=column_maps[1])
    logger.info(f"[JOIN] left cols: {list(left_df.columns)[:10]}, left_keys={left_key_cols}")
    logger.info(f"[JOIN] right cols: {list(right_df.columns)[:10]}, right_keys={right_key_cols}")

    # Garanzia finale: rimuovi eventuali colonne duplicate residue
    left_df = drop_duplicate_columns(left_df, keep='first')
    right_df = drop_duplicate_columns(right_df, keep='first')

    # Stable source-order marker for partitioned left/right joins. Null-key
    # partitions are concatenated after the mergeable rows; without this
    # marker a left join silently moves all null-key rows to the end.
    join_how = str(how).lower()
    order_token = uuid.uuid4().hex
    left_order_marker = None
    right_order_marker = None
    if join_how in {"left", "outer"}:
        left_order_marker = f"__plenora_join_left_order_{order_token}"
        left_df[left_order_marker] = np.arange(len(left_df), dtype=np.int64)
    if join_how in {"right", "outer"}:
        right_order_marker = f"__plenora_join_right_order_{order_token}"
        right_df[right_order_marker] = np.arange(len(right_df), dtype=np.int64)

    # Guard post-rename [internal]: dopo la pre-validation in testa a
    # op_join, la chiave DEVE esistere qui. Se raggiungiamo questo raise
    # significa che safe_rename ha un bug (ha rinominato/perso la chiave
    # nonostante la pre-validation). Non e' un errore utente, e' una
    # regressione interna: diagnosticarla come tale
    missing_left_after = [key for key in left_key_cols if key not in left_df.columns]
    if missing_left_after:
        raise KeyError(
            f"[internal] chiavi join sinistre {missing_left_after} scomparse dopo safe_rename "
            f"nonostante pre-validation. Possibile bug interno in op_join.safe_rename. "
            f"Colonne dopo rename: {list(left_df.columns)}"
        )
    missing_right_after = [key for key in right_key_cols if key not in right_df.columns]
    if missing_right_after:
        raise KeyError(
            f"[internal] chiavi join destre {missing_right_after} scomparse dopo safe_rename "
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
    left_null_masks = {key: left_df[key].isna() for key in left_key_cols}
    right_null_masks = {key: right_df[key].isna() for key in right_key_cols}
    left_null_keys = sum(int(mask.sum()) for mask in left_null_masks.values())
    right_null_keys = sum(int(mask.sum()) for mask in right_null_masks.values())

    if left_null_keys or right_null_keys:
        effetto = (
            "escluse dal risultato" if how == 'inner'
            else "preservate senza match"
        )
        logger.warning(
            f"Join {how}: chiavi NaN — sinistra {left_key_cols}={left_null_keys}, "
            f"destra {right_key_cols}={right_null_keys}. pandas non matcha "
            f"NaN==NaN, le righe key-NaN saranno {effetto}."
        )

    # Cardinality guard many-to-many: un join `inner` con 50k righe x 50k
    # righe su chiave a
    # cardinalita' alta (es. solo 10 valori distinti) produceva 50k * 50k
    # / 10 = 250M righe — OOM immediato. Mirror del guard di cross_join.
    # Stima cap pre-merge con bound peggiore basato sulle frequenze di
    # chiave; se supera la soglia, fail-closed con messaggio
    # operativo invece di OOM silente.
    #
    # Escludi i null dal prodotto common-keys. La policy downstream
    # sostituisce ogni null con una sentinel UNIVOCA per riga
    # (NaN NON matcha NaN cross-side, vedi commento sopra). Quindi nella
    # stima: left_nulls * right_nulls non e' una croce match-able — vanno
    # contati solo come righe preservate per how in (left/right/outer).
    # I null non entrano nei common-key matchable: includerli sovrastima
    # join legittimi con molti null bilaterali.
    _MAX_JOIN_OUTPUT_ROWS = 50_000_000  # 50M righe = ~5-10GB peak tipico
    estimated_rows = None
    try:
        estimated_rows = _estimate_join_output_rows_by_keys(
            left_df, right_df, left_key_cols, right_key_cols, how
        )
        if estimated_rows is not None and estimated_rows > _MAX_JOIN_OUTPUT_ROWS:
            raise ValueError(
                f"Join {how} su chiavi ({left_key_cols} x {right_key_cols}) "
                f"produrrebbe ~{estimated_rows:,} righe (cap "
                f"{_MAX_JOIN_OUTPUT_ROWS:,}). Probabile many-to-many "
                f"con chiavi a bassa cardinalita'. Verifica le chiavi "
                f"o riduci le tabelle in input."
            )
    except ValueError:
        raise
    except Exception as exc:
        # Warning con context completo (tipi chiave, exception type) cosi'
        # il bypass del guard resta visibile. Il guard salta ancora per non
        # bloccare join legittimi su dtype esotici.
        logger.warning(
            f"[GUARD-BYPASS] Join cardinality guard skipped: "
            f"left_keys={left_key_cols}, right_keys={right_key_cols}, "
            f"how={how}, exc_type={type(exc).__name__}: {exc}"
        )

    left_any_null = (
        np.logical_or.reduce(list(left_null_masks.values()))
        if left_null_masks else np.zeros(len(left_df), dtype=bool)
    )
    right_any_null = (
        np.logical_or.reduce(list(right_null_masks.values()))
        if right_null_masks else np.zeros(len(right_df), dtype=bool)
    )
    use_null_partition = (
        left_null_keys + right_null_keys >= _NULL_PARTITION_MIN_ROWS
        and str(how).lower() in {"inner", "left", "right", "outer"}
    )

    merge_left = left_df
    merge_right = right_df
    merge_estimated_rows = estimated_rows
    if use_null_partition:
        merge_left = left_df.loc[~left_any_null]
        merge_right = right_df.loc[~right_any_null]
        merge_estimated_rows = _estimate_join_output_rows_by_keys(
            merge_left,
            merge_right,
            left_key_cols,
            right_key_cols,
            how,
        )
        logger.info(
            "[JOIN-NULL-PARTITION] how=%s mergeable_left=%s/%s "
            "mergeable_right=%s/%s estimated_merge_rows=%s",
            how,
            len(merge_left),
            len(left_df),
            len(merge_right),
            len(right_df),
            merge_estimated_rows,
        )

    hash_partition_count = 1
    if (
        merge_estimated_rows is not None
        and str(how).lower() == "left"
        and len(merge_left) >= _HASH_PARTITION_MIN_ROWS
    ):
        hash_partition_count = plan_merge_partitions(
            merge_left,
            merge_right,
            merge_estimated_rows,
        )
        if hash_partition_count > 1:
            logger.info(
                "[JOIN-HASH-PARTITION] left_rows=%s partitions=%s",
                len(merge_left),
                hash_partition_count,
            )

    if merge_estimated_rows is not None and hash_partition_count == 1:
        guard_estimated_merge_memory(
            merge_left,
            merge_right,
            merge_estimated_rows,
            stage=JOIN_MEMORY_STAGE,
            path_basename="join_output",
        )

    _join_nan_token = uuid.uuid4().hex[:12]
    _left_sentinel_prefix = f"__JOIN_NULL_L_{_join_nan_token}_"
    _right_sentinel_prefix = f"__JOIN_NULL_R_{_join_nan_token}_"

    if (left_null_keys or right_null_keys) and not use_null_partition:
        # Forza object dtype su ENTRAMBE le key (anche quella senza null
        # se ne casta una sola, pandas merge solleva "trying to merge object
        # and float64 columns". L'object dtype ospita le sentinel string
        # accanto ai valori originali; ripristineremo NaN dopo il merge
        for key_idx, (left_key, mask) in enumerate(left_null_masks.items()):
            null_count = int(mask.sum())
            left_df[left_key] = left_df[left_key].astype(object)
            if null_count:
                set_values_where_mask(
                    left_df,
                    mask,
                    left_key,
                    [f"{_left_sentinel_prefix}{key_idx}_{i}" for i in range(null_count)],
                )
        for key_idx, (right_key, mask) in enumerate(right_null_masks.items()):
            null_count = int(mask.sum())
            right_df[right_key] = right_df[right_key].astype(object)
            if null_count:
                set_values_where_mask(
                    right_df,
                    mask,
                    right_key,
                    [f"{_right_sentinel_prefix}{key_idx}_{i}" for i in range(null_count)],
                )

    # Esegui merge
    if hash_partition_count > 1:
        merged_partitions = []
        partition_size = math.ceil(len(merge_left) / hash_partition_count)
        for start in range(0, len(merge_left), partition_size):
            left_partition = merge_left.iloc[start:start + partition_size]
            partition_rows = _estimate_join_output_rows_by_keys(
                left_partition,
                merge_right,
                left_key_cols,
                right_key_cols,
                how,
            )
            if partition_rows is not None:
                guard_estimated_merge_memory(
                    left_partition,
                    merge_right,
                    partition_rows,
                    stage=JOIN_MEMORY_STAGE,
                    path_basename="join_output_partition",
                )
            merged_partitions.append(
                merge_dataframes(
                    left_partition,
                    merge_right,
                    left_on=left_key_cols,
                    right_on=right_key_cols,
                    how=how,
                    suffixes=("", "_dup"),
                )
            )
        guard_estimated_concat_memory(
            merged_partitions,
            stage=JOIN_MEMORY_STAGE,
            path_basename="join_output_partition_concat",
        )
        result = concat_dataframes(merged_partitions, ignore_index=True)
    else:
        result = merge_dataframes(
            merge_left, merge_right,
            left_on=left_key_cols,
            right_on=right_key_cols,
            how=how,
            suffixes=('', '_dup')
        )

    if use_null_partition:
        null_partitions = []
        join_how = str(how).lower()
        if left_null_keys and join_how in {"left", "outer"}:
            null_partitions.append(
                merge_dataframes(
                    left_df.loc[left_any_null],
                    right_df.head(0),
                    left_on=left_key_cols,
                    right_on=right_key_cols,
                    how="left",
                    suffixes=("", "_dup"),
                )
            )
        if right_null_keys and join_how in {"right", "outer"}:
            null_partitions.append(
                merge_dataframes(
                    left_df.head(0),
                    right_df.loc[right_any_null],
                    left_on=left_key_cols,
                    right_on=right_key_cols,
                    how="right",
                    suffixes=("", "_dup"),
                )
            )
        if null_partitions:
            concat_parts = [result, *null_partitions]
            guard_estimated_concat_memory(
                concat_parts,
                stage=JOIN_MEMORY_STAGE,
                path_basename="join_output_null_concat",
            )
            result = concat_dataframes(
                concat_parts,
                ignore_index=True,
            )

    order_columns = [
        marker
        for marker in (left_order_marker, right_order_marker)
        if marker is not None and marker in result.columns
    ]
    if order_columns:
        needs_restore = (
            len(order_columns) > 1
            or not result[order_columns[0]].is_monotonic_increasing
        )
        if needs_restore:
            guard_estimated_concat_memory(
                [result],
                stage=JOIN_MEMORY_STAGE,
                path_basename="join_output_order_restore",
            )
            result = result.sort_values(
                order_columns,
                kind="stable",
                na_position="last",
                ignore_index=True,
            )
        result = result.drop(columns=order_columns)

    # Ripristina NaN nelle posizioni dei sentinel sulle key di output
    if (left_null_keys or right_null_keys) and not use_null_partition:
        def _restore_join_nans(series):
            if series.dtype != object:
                return series
            str_view = series.astype(str)
            is_sentinel = (
                str_view.str.startswith(_left_sentinel_prefix, na=False)
                | str_view.str.startswith(_right_sentinel_prefix, na=False)
            )
            return series.mask(is_sentinel, other=np.nan)

        for key in left_key_cols:
            if key in result.columns:
                result[key] = _restore_join_nans(result[key])
        for key in right_key_cols:
            if key in result.columns:
                result[key] = _restore_join_nans(result[key])

    # Rimuovi colonna join duplicata se nomi diversi
    right_keys_to_drop = [
        right_key
        for left_key, right_key in zip(left_key_cols, right_key_cols)
        if left_key != right_key and right_key not in left_key_cols and right_key in result.columns
    ]
    if right_keys_to_drop and column_maps is None:
        result = result.drop(columns=[*dict.fromkeys(right_keys_to_drop)])

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

    _output_rows = len(left_df) * len(right_df)
    if _cap_rows > 0:
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

    guard_estimated_merge_memory(
        left_df,
        right_df,
        _output_rows,
        stage=CROSS_JOIN_MEMORY_STAGE,
        path_basename="cross_join_output",
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
