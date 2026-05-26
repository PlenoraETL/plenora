"""
Cluster RESHAPE — operazioni di trasformazione struttura tabella.

Keeps transformation behavior isolated from orchestration callers.
"""

from typing import List, Optional, Union

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.ports import FrameLike
from app.core.dataframes.tabular import (
    concat_non_null_as_strings,
    empty_dataframe,
    is_multiindex,
    melt_dataframe,
    pivot_table_dataframe,
    table_diff_dataframe,
)
from app.core.manipola.types import StringMap
from app.utils.logger import get_logger

logger = get_logger("app.core.manipola.transforms.base")

DataFrame = FrameLike


# PIVOT TABLE
# Trasforma righe in colonne: la colonna pivot_col viene "spalmata"
# orizzontalmente, i valori di value_col diventano i contenuti
# e index_col diventa la chiave primaria del risultato
# Esempio classico ICSC
# immobile | tipo_valore | importo immobile | canone | rendita
# A | canone | 1000 A | 1000 | 500
# A | rendita | 500 B | 2000 | NULL
# B | canone | 2000

def op_pivot(
    df: DataFrame,
    index_col: str,
    pivot_col: str,
    value_col: str,
    aggr_func: str = "first",
    mapping: Optional[StringMap] = None
) -> DataFrame:
    """
    Tabella pivot: trasforma righe in colonne.

    Prende i valori distinti di `pivot_col`, li "spalma" come colonne,
    e riempie ciascuna con il valore corrispondente di `value_col`,
    aggregato tramite `aggr_func`.

    Args:
        df: DataFrame input
        index_col: Colonna chiave (diventa la PK del risultato).
                   Accetta anche più colonne separate da virgola.
        pivot_col: Colonna che contiene i "tipi" da spalmare come colonne
        value_col: Colonna con i valori da estrarre
        aggr_func: Funzione di aggregazione da applicare quando ci sono
                   duplicati (index_col + pivot_col non univoci).
                   Valori: first | last | max | min | sum | mean | count | concat
        mapping: Dizionario opzionale { valore_pivot_col → nome_colonna_output }.
                 Se fornito, vengono create SOLO le colonne nel mapping;
                 valori di pivot_col non nel mapping vengono scartati.
                 Se vuoto/None, una colonna per ogni valore unico di pivot_col.

    Returns:
        DataFrame "pivotato" con index_col come colonna(e) e una colonna
        per ogni valore (o mapping) di pivot_col.

    Example:
        # Input:
        # immobile | tipo | importo
        # A | canone | 1000
        # A | rendita | 500
        # B | canone | 2000

        result = op_pivot(
            df,
            index_col="immobile",
            pivot_col="tipo",
            value_col="importo",
            aggr_func="max",
            mapping={"canone": "val_canone", "rendita": "val_rendita"}
        )
        # Output:
        # immobile | val_canone | val_rendita
        # A | 1000 | 500
        # B | 2000 | NaN
    """
    if not index_col or not pivot_col or not value_col:
        logger.warning("Pivot: parametri obbligatori mancanti (index_col, pivot_col, value_col)")
        return df

    # Supporto multi-index: "col1, col2" → ["col1", "col2"
    if isinstance(index_col, str) and ',' in index_col:
        index_cols = [c.strip() for c in index_col.split(',') if c.strip()]
    elif isinstance(index_col, list):
        index_cols = index_col
    else:
        index_cols = [index_col]

    # Verifica esistenza colonne
    all_needed = index_cols + [pivot_col, value_col]
    missing = [c for c in all_needed if c not in df.columns]
    if missing:
        logger.warning(f"[GESTITO] Pivot: colonne mancanti: {missing}")
        return df

    result = copy_frame_for_update(df)
    result[pivot_col] = result[pivot_col].fillna('').astype(str).str.strip()
    try:
        if mapping:
            allowed_values = list(mapping.keys())
            result = result[result[pivot_col].isin(allowed_values)]
            if result.empty:
                logger.warning(
                    f"Pivot: nessun valore di '{pivot_col}' corrisponde "
                    f"al mapping {allowed_values}"
                )
                return df

        aggr_func_lower = aggr_func.lower().strip() if aggr_func else 'first'

        if aggr_func_lower == 'concat':
            # Aggregazione speciale: concatena come stringa separata da virgola
            agg_fn = concat_non_null_as_strings
        elif aggr_func_lower in ('first', 'last', 'max', 'min', 'sum', 'mean', 'count'):
            agg_fn = aggr_func_lower
        else:
            logger.warning(f"Pivot: funzione '{aggr_func}' non supportata, uso 'first'")
            agg_fn = 'first'

        pivoted = pivot_table_dataframe(
            result,
            index=index_cols,
            columns=pivot_col,
            values=value_col,
            aggfunc=agg_fn
        )

        # Flatten MultiIndex colonne
        if is_multiindex(pivoted.columns):
            pivoted.columns = [
                '_'.join(str(c) for c in col).strip('_')
                for col in pivoted.columns
            ]
        else:
            pivoted.columns = [str(c) for c in pivoted.columns]

        # Rimuovi nome asse colonne (metadata pandas
        pivoted.columns.name = None

        # Riporta index_cols come colonne normali
        pivoted = pivoted.reset_index()

        if mapping:
            rename_map = {}
            for pivot_val, output_name in mapping.items():
                pivot_val_str = str(pivot_val)
                if pivot_val_str in pivoted.columns:
                    rename_map[pivot_val_str] = output_name
                else:
                    logger.warning(
                        f"Pivot: valore '{pivot_val}' non trovato "
                        f"nei risultati pivot"
                    )

            if rename_map:
                pivoted = pivoted.rename(columns=rename_map)

            # Tieni solo le colonne index + quelle mappate
            keep_cols = index_cols + list(rename_map.values())
            keep_cols = [c for c in keep_cols if c in pivoted.columns]
            pivoted = pivoted[keep_cols]

        logger.info(
            f"Pivot: {len(df)} righe -> {len(pivoted)} righe x "
            f"{len(pivoted.columns)} colonne "
            f"(index={index_cols}, pivot={pivot_col}, "
            f"value={value_col}, aggr={aggr_func_lower})"
        )
        return pivoted

    except Exception as e:
        logger.error(f"[CRASH] Errore Pivot: {e}", exc_info=True)
        raise


# TABLE DIFF (Confronta Tabelle

def op_table_diff(
    inputs: List[DataFrame],
    left_keys: Union[str, List[str]] = "",
    right_keys: Union[str, List[str]] = "",
    compare_columns: Union[str, List[str], None] = None,
    include_unchanged: str = "no",
    separator: str = "#"
) -> DataFrame:
    """
    Confronta due tabelle e identifica righe aggiunte, eliminate e modificate.

    Args:
        inputs: Lista di 2 DataFrame [vecchia (SX), nuova (DX)]
        left_keys: Colonne chiave tabella vecchia (SX)
        right_keys: Colonne chiave tabella nuova (DX)
        compare_columns: Colonne da confrontare (vuoto = tutte non-chiave)
        include_unchanged: "yes" per includere righe invariate
        separator: Separatore per _diff_columns e _diff_old_values (default: #)

    Returns:
        DataFrame con colonne:
        - _diff_status: ADDED, DELETED, MODIFIED, UNCHANGED
        - _diff_columns: colonne modificate (separate da separator)
        - _diff_old_values: valori precedenti (stesso ordine, stesso separator)
    """
    if len(inputs) < 2:
        logger.warning("Table Diff richiede 2 input")
        return inputs[0] if inputs else empty_dataframe()

    old_df = copy_frame_for_update(inputs[0])
    new_df = copy_frame_for_update(inputs[1])
    sep = separator.strip() if separator else "#"

    if isinstance(left_keys, str):
        left_keys = [k.strip() for k in left_keys.split(",") if k.strip()]
    if isinstance(right_keys, str):
        right_keys = [k.strip() for k in right_keys.split(",") if k.strip()]

    if not left_keys or not right_keys:
        logger.warning("[GESTITO] Table Diff: specificare le colonne chiave per entrambe le tabelle")
        return old_df

    if len(left_keys) != len(right_keys):
        logger.error(
            f"Table Diff: numero chiavi diverso "
            f"(SX: {len(left_keys)}, DX: {len(right_keys)})"
        )
        return old_df

    # Verifica che le chiavi esistano
    for k in left_keys:
        if k not in old_df.columns:
            logger.warning(f"Table Diff: chiave '{k}' non trovata in tabella SX")
            return old_df
    for k in right_keys:
        if k not in new_df.columns:
            logger.warning(f"Table Diff: chiave '{k}' non trovata in tabella DX")
            return old_df

    if isinstance(compare_columns, str):
        compare_columns = [c.strip() for c in compare_columns.split(",") if c.strip()]
    if not compare_columns:
        compare_columns = None  # confronta tutte

    key_rename = {}
    for lk, rk in zip(left_keys, right_keys):
        if lk != rk:
            key_rename[rk] = lk
    if key_rename:
        new_df = new_df.rename(columns=key_rename)

    # Le chiavi unificate (nomi SX
    key_cols = list(left_keys)

    for k in key_cols:
        if old_df[k].dtype != new_df[k].dtype:
            old_df[k] = old_df[k].astype(str)
            new_df[k] = new_df[k].astype(str)

    old_value_cols = [c for c in old_df.columns if c not in key_cols]
    new_value_cols = [c for c in new_df.columns if c not in key_cols]
    common_value_cols = [c for c in old_value_cols if c in new_value_cols]

    if compare_columns:
        common_value_cols = [c for c in compare_columns if c in common_value_cols]

    # Colonne presenti solo in una tabella
    only_old_cols = [c for c in old_value_cols if c not in new_value_cols]
    only_new_cols = [c for c in new_value_cols if c not in old_value_cols]

    # MANIPOLA-7 (2026-05-26): trim pre-merge per RAM.
    # Pre-fix: `table_diff_dataframe` riceveva i DataFrame INTERI anche
    # quando `compare_columns` selezionava poche colonne. Il merge interno
    # produceva un DataFrame con `_OLD`/`_NEW` per OGNI colonna delle 2
    # tabelle, anche per quelle non confrontate — picco RAM proporzionale
    # al numero totale di colonne, non al subset compare.
    # Post-fix: subset esplicito a `key_cols + common_value_cols +
    # only_old_cols/only_new_cols` PRIMA di passarli al diff helper.
    # Le colonne non rilevanti (es. timestamp tecnici, metadata) vengono
    # droppate prima del merge.
    relevant_old_cols = list(set(key_cols + common_value_cols + only_old_cols))
    relevant_new_cols = list(set(key_cols + common_value_cols + only_new_cols))
    # Preserve l'ordine originale per stabilita' output column.
    old_df_trim = old_df[[c for c in old_df.columns if c in relevant_old_cols]]
    new_df_trim = new_df[[c for c in new_df.columns if c in relevant_new_cols]]

    result_df, status_counts = table_diff_dataframe(
        old_df_trim,
        new_df_trim,
        key_cols=key_cols,
        common_value_cols=common_value_cols,
        only_old_cols=only_old_cols,
        only_new_cols=only_new_cols,
        include_unchanged=include_unchanged,
        separator=sep,
    )

    if len(result_df) == 0:
        logger.info("Table Diff: nessuna differenza trovata")
        return result_df
    logger.info(
        f"Table Diff completato: {len(result_df)} righe totali — "
        f"ADDED: {status_counts.get('ADDED', 0)}, "
        f"DELETED: {status_counts.get('DELETED', 0)}, "
        f"MODIFIED: {status_counts.get('MODIFIED', 0)}, "
        f"UNCHANGED: {status_counts.get('UNCHANGED', 0)}"
    )

    return result_df


# MELT (UNPIVOT
# Trasforma colonne in righe: da formato wide a formato long

def op_melt(
    df: DataFrame,
    id_columns: List[str],
    value_columns: Optional[List[str]] = None,
    var_name: str = "variable",
    value_name: str = "value"
) -> DataFrame:
    """
    Unpivot: trasforma colonne in righe (wide → long).

    Args:
        df: DataFrame input
        id_columns: Colonne da mantenere fisse (identificatori)
        value_columns: Colonne da sciogliere in righe (None = tutte le altre)
        var_name: Nome della colonna che conterrà i nomi delle variabili
        value_name: Nome della colonna che conterrà i valori

    Returns:
        DataFrame in formato long
    """
    try:
        # Filtra colonne esistenti
        id_columns = [c for c in id_columns if c in df.columns]
        if value_columns:
            value_columns = [c for c in value_columns if c in df.columns]
            if not value_columns:
                value_columns = None

        # `value_name` collide se il DataFrame ha gia' una colonna con
        # quel nome — pandas.melt allora rifiuta con ValueError. I
        # default registry ("variable"/"value") sono nomi comuni: lo
        # auto-rinominiamo con un suffisso per non far crashare nodi
        # configurati con default su sorgenti che contengono "value".
        df_cols = set(df.columns)

        def _avoid_collision(name: str) -> str:
            if name not in df_cols:
                return name
            for i in range(1, 100):
                cand = f"{name}_{i}"
                if cand not in df_cols:
                    logger.warning(
                        f"Melt: '{name}' collide con una colonna sorgente, "
                        f"rinominato in '{cand}'"
                    )
                    return cand
            return name  # fallback (improbabile)

        var_name = _avoid_collision(var_name)
        value_name = _avoid_collision(value_name)

        result = melt_dataframe(
            df,
            id_vars=id_columns or None,
            value_vars=value_columns,
            var_name=var_name,
            value_name=value_name
        )

        logger.info(
            f"Melt: {len(df)} righe x {len(df.columns)} colonne -> "
            f"{len(result)} righe x {len(result.columns)} colonne"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Melt: {e}", exc_info=True)
        raise


# TRANSPOSE — Trasponi righe e colonne
# Scambia righe e colonne del DataFrame

def op_transpose(
    df: DataFrame,
    id_column: str = None,
    output_columns: list = None,
) -> DataFrame:
    """
    Trasposizione del DataFrame (righe ↔ colonne).

    Args:
        df: DataFrame input
        id_column: Colonna da usare come intestazioni delle nuove colonne.
                   Se None, traspone con indice numerico.
        output_columns: Nomi espliciti per le colonne post-traspose.
            Se forniti e di lunghezza compatibile, rinominano sequenzialmente
            le colonne risultanti (escludendo la prima, se ``id_column`` e'
            presente, che mantiene il nome originale). Il parametro arriva
            dal registry: senza questo arg, qualunque nodo configurato
            dal UI crashava con ``TypeError: unexpected keyword argument
            'output_columns'``.

    Returns:
        DataFrame trasposto
    """
    if len(df) == 0:
        return df

    try:
        original_shape = df.shape

        if id_column and id_column in df.columns:
            # Usa la colonna indicata come nuove intestazioni
            result = df.set_index(id_column).T.reset_index()
            result = result.rename(columns={"index": id_column})
        else:
            result = df.T.reset_index()
            result.columns = [f"col_{i}" for i in range(len(result.columns))]

        if output_columns:
            # Rinomina mantenendo la prima colonna (id/index) e applicando
            # i nomi forniti alle restanti, per evitare collisioni quando
            # ``output_columns`` ne ha meno o piu' del necessario.
            current = list(result.columns)
            new_cols = current.copy()
            for i, name in enumerate(output_columns, start=1):
                if i < len(new_cols) and name:
                    new_cols[i] = str(name)
            result.columns = new_cols

        logger.info(
            f"Transpose completato: {original_shape} -> {result.shape}"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Transpose: {e}", exc_info=True)
        raise
