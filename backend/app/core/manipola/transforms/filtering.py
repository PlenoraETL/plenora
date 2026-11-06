"""
Cluster FILTERING — selezione righe e logica condizionale colonna.

Keeps transformation behavior isolated from orchestration callers.
"""

from typing import Any

import numpy as np

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.ports import FrameLike
from app.core.dataframes.tabular import (
    coerce_numeric_series,
    false_boolean_series,
    is_numeric_series,
    set_values_where_mask,
    unique_non_null_column_values,
)
from app.core.manipola.types import JsonRows
from app.shared.execution.condition_groups import condition_groups
from app.utils.logger import get_logger

logger = get_logger(__name__)

DataFrame = FrameLike


# FILTRO RIGHE
# Applica condizioni su colonne con operatori: =, !=, >, <, >=, <=, LIKE, IN, IS NULL
#
# Gruppi E/O (06/11/2026, ``shared.execution.condition_groups``): ogni voce di
# ``conditions`` puo' avere ``group`` (intero >= 0, assente = 0). Dentro un
# gruppo le condizioni sono in AND e si applicano IN SEQUENZA sulle righe
# rimaste, come prima dei gruppi (stesse conversioni, stessi errori: una
# condizione vede solo le righe che hanno passato le precedenti del gruppo).
# Tra gruppi OR: restano le righe che passano almeno un gruppo, nell'ordine
# originale, indice azzerato. Un solo gruppo = il risultato di sempre.

FILTER_OPERATORS = frozenset({
    "==", "!=", ">", ">=", "<", "<=", "contains", "startswith", "endswith",
    "isnull", "notnull", "in", "not_in",
})


def prepare_filter(df: DataFrame, column: str | None = None, operator: str = "==", value: Any = None, conditions: JsonRows | None = None) -> list | None:
    """Validate filter configuration against columns and dtypes, without filtering."""
    if conditions is not None:
        if not isinstance(conditions, list):
            raise ValueError("Filter: conditions deve essere una lista.")
        for condition in conditions:
            if not isinstance(condition, dict) or not condition.get("column") or not condition.get("operator"):
                raise ValueError("Filter: ogni condizione richiede column e operator.")
            if condition["operator"] not in FILTER_OPERATORS:
                raise ValueError(f"Filter: operatore '{condition['operator']}' non supportato.")
            prepare_filter(df, condition["column"], condition["operator"], condition.get("value"))
        # ``group`` di ogni voce: stesso errore in Valida (esecuzione a vuoto)
        # e in esecuzione.
        condition_groups(conditions)
        return

    if column not in df.columns:
        # Fail-fast: un filtro che non filtra e' un bug grave. Silent return
        # df invariato nascondeva l'errore e i conteggi a valle erano sballati
        available = list(df.columns)
        raise ValueError(
            f"Filter: colonna '{column}' non trovata. "
            f"Colonne disponibili: {available}"
        )

    # Path read-only puro: niente modifiche column-level, leggi direttamente
    # da `df`.
    col = df[column]

    if operator in {"in", "not_in"}:
        if not isinstance(value, (str, list)):
            raise ValueError("Filter: in/not_in richiede una lista o stringa separata da virgole.")
        values = value.split(",") if isinstance(value, str) else value
        values = [item.strip() if isinstance(item, str) else item for item in values]
        if is_numeric_series(col):
            try:
                values = [float(item) if item is not None else None for item in values]
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Filter: valori non numerici per colonna '{column}'.") from exc
        return values



def op_filter(
    df: DataFrame,
    column: str | None = None,
    operator: str = "==",
    value: Any = None,
    conditions: JsonRows | None = None
) -> DataFrame:
    """
    Filtra righe in base a una condizione o a una lista di condizioni.

    Args:
        df: DataFrame input
        column: Colonna da filtrare
        operator: Operatore (==, !=, >, >=, <, <=, contains, startswith, endswith, isnull, notnull)
        value: Valore da confrontare
        conditions: Lista {column, operator, value, group?}; se presente
            sostituisce il filtro singolo (AND nel gruppo, OR tra gruppi).
            Lista vuota: passthrough.

    Returns:
        DataFrame filtrato
    """
    prepared_values = prepare_filter(df, column, operator, value, conditions)
    if conditions is not None:
        groups = condition_groups(conditions)
        if not groups:
            return df
        applied = [_apply_group(df, group) for group in groups]
        if len(applied) == 1:
            # Un solo gruppo: lo stesso DataFrame del filtro a catena di sempre.
            return applied[0][1].reset_index(drop=True)
        kept = applied[0][0]
        for positions, _ in applied[1:]:
            kept = np.union1d(kept, positions)
        result = df.iloc[kept].reset_index(drop=True)
        logger.info(f"Filter: {len(df)} -> {len(result)} righe ({len(groups)} gruppi in O)")
        return result

    selected = _apply_condition(df, column, operator, value, prepared_values)
    if selected is None:
        return df
    return selected[1].reset_index(drop=True)


def _apply_group(df: DataFrame, group: JsonRows) -> tuple[Any, DataFrame]:
    """Condizioni di un gruppo in AND -> (posizioni crescenti in ``df``, righe).

    In sequenza sulle righe rimaste, come il filtro a catena di prima: la
    preparazione (valori ``in``) e la valutazione di ogni condizione vedono
    solo le righe che hanno passato le precedenti del gruppo.
    """
    positions = np.arange(len(df))
    frame = df
    for condition in group:
        column, operator, value = condition["column"], condition["operator"], condition.get("value")
        prepared = prepare_filter(frame, column, operator, value)
        mask, frame = _apply_condition(frame, column, operator, value, prepared)
        positions = positions[np.asarray(mask, dtype=bool)]
    return positions, frame


def _apply_condition(df: DataFrame, column: str, operator: str, value: Any, prepared_values: list | None) -> tuple[Any, DataFrame] | None:
    """UNA condizione su ``df`` -> (maschera, righe selezionate con l'indice originale).

    Unica valutazione degli operatori per il filtro singolo e per i gruppi.
    ``None`` per un operatore ignoto (solo il filtro singolo ci arriva: la
    lista ``conditions`` lo rifiuta in ``prepare_filter``).
    """
    # Path read-only puro: niente modifiche column-level, leggi direttamente
    # da `df`.
    col = df[column]

    if operator in {"in", "not_in"}:
        mask = col.isin(prepared_values)
        mask = ~mask if operator == "not_in" else mask
        return mask, df[mask]

    # Se la colonna è numerica e il valore è stringa, converti
    if value is not None and is_numeric_series(col):
        try:
            # Prova int prima, poi float
            if '.' in str(value):
                value = float(value)
            else:
                value = float(value)  # anche int passa per float per gestire NaN
        except (ValueError, TypeError):
            logger.warning(f"Filter: impossibile convertire '{value}' a numero per colonna {column}")

    try:
        if operator == '==':
            mask = col == value
        elif operator == '!=':
            mask = col != value
        elif operator == '>':
            # value e' gia' convertito dall'auto-conversione sopra; se non
            # convertibile, resta stringa e il confronto fallisce.
            mask = col > value
        elif operator == '>=':
            mask = col >= value
        elif operator == '<':
            mask = col < value
        elif operator == '<=':
            mask = col <= value
        elif operator == 'contains':
            # SECURITY: regex=False tratta il valore come stringa letterale
            # Prima: regex=True (default) → un utente poteva iniettare pattern
            # catastrofici come ((a+)+)+b causando ReDoS (CPU hang sul worker
            mask = col.astype(str).str.contains(str(value), case=False, na=False, regex=False)
        elif operator == 'startswith':
            mask = col.astype(str).str.startswith(str(value), na=False)
        elif operator == 'endswith':
            mask = col.astype(str).str.endswith(str(value), na=False)
        elif operator == 'isnull':
            mask = col.isnull()
        elif operator == 'notnull':
            mask = col.notnull()
        else:
            logger.warning(f"Operatore '{operator}' non supportato")
            return None

        # La selezione resta dentro il try come prima: un errore pandas nel
        # selezionare con la maschera e' lo stesso ValueError.
        result = df[mask]
        logger.info(f"Filter: {len(df)} -> {len(result)} righe")
        return mask, result

    # intent: fail-closed — input utente invalido (operatore/valore
    # incompatibile con tipo colonna, runtime error pandas) solleva
    # ValueError con messaggio safe. str(e) tecnico preservato solo
    # nel logger.warning server-side per postmortem admin
    except Exception as e:
        logger.warning(f"[GESTITO] Errore Filter '{operator}' su colonna '{column}': {e}")
        raise ValueError(
            f"Filter: operatore '{operator}' non applicabile sulla colonna "
            f"'{column}' con il valore fornito. Verificare tipo e sintassi."
        ) from e


# CONDIZIONALE (CASE WHEN
# Crea colonna con valore dipendente da condizioni (simile a SQL CASE WHEN

def op_conditional(
    df: DataFrame,
    column: str,
    conditions: JsonRows,
    default_value: str = "",
    output_column: str = "result"
) -> DataFrame:
    """
    Crea colonna con logica case/when su condizioni multiple.

    Args:
        df: DataFrame input
        column: Colonna da valutare
        conditions: Lista di {operator, value, result}
            operator: ==, !=, >, >=, <, <=, contains, startswith, endswith, isnull, notnull, between
            value: Valore di confronto (o "min,max" per between)
            result: Valore da assegnare se la condizione è vera
        default_value: Valore se nessuna condizione è soddisfatta
        output_column: Nome colonna output

    Returns:
        DataFrame con nuova colonna
    """
    if not column or column not in df.columns:
        logger.warning(f"Conditional: colonna '{column}' non trovata")
        return df

    if not conditions:
        logger.warning("Conditional: nessuna condizione definita")
        return df

    result = copy_frame_for_update(df)

    try:
        # Log diagnostico: valori unici della colonna sorgente
        unique_vals = unique_non_null_column_values(result, column)
        logger.info(f"Conditional: colonna '{column}' - {len(unique_vals)} valori unici, sample: {list(unique_vals[:10])}, dtype: {result[column].dtype}")

        # Inizializza con default
        result[output_column] = default_value

        col_values = result[column]

        # Applica condizioni in ordine inverso (la prima ha priorità
        total_matched = 0
        for cond in reversed(conditions):
            op = cond.get('operator', '==')
            val = cond.get('value', '')
            res = cond.get('result', '')

            try:
                mask = _evaluate_condition(col_values, op, val)
                match_count = mask.sum()
                total_matched += match_count
                set_values_where_mask(result, mask, output_column, res)
                logger.info(f"Conditional:   {op} '{val}' -> '{res}' : {match_count} righe matchate")
            except Exception as e:
                logger.warning(f"Conditional: errore condizione {op} {val}: {e}")
                continue

        logger.info(f"Conditional: totale matchate={total_matched}, non matchate={len(result) - total_matched} (default='{default_value}')")

        # Auto-detect tipo numerico: se tutti i result values (e default
        # sono numerici, converti la colonna a numerico
        all_values = [cond.get('result', '') for cond in conditions] + [default_value]
        all_numeric = True
        for v in all_values:
            if v is None or str(v).strip() == '':
                continue  # vuoto/None ok, diventa NaN
            try:
                float(str(v).replace(',', '.'))
            except (ValueError, TypeError):
                all_numeric = False
                break

        if all_numeric:
            result[output_column] = coerce_numeric_series(result[output_column])
            logger.info(f"Conditional: '{output_column}' convertita a numerico (dtype: {result[output_column].dtype})")

        logger.info(f"Conditional: {column} -> {output_column} ({len(conditions)} condizioni)")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Conditional: {e}", exc_info=True)
        raise


def _evaluate_condition(col_values, operator: str, value: str):
    """Valuta una condizione e restituisce una maschera booleana."""

    if operator == 'isnull':
        return col_values.isna()
    elif operator == 'notnull':
        return col_values.notna()

    # Prova conversione numerica del valore
    numeric_val = None
    try:
        numeric_val = float(value)
    except (ValueError, TypeError):
        pass

    if operator == '==':
        if numeric_val is not None:
            return coerce_numeric_series(col_values) == numeric_val
        return col_values.astype(str) == str(value)

    elif operator == '!=':
        if numeric_val is not None:
            return coerce_numeric_series(col_values) != numeric_val
        return col_values.astype(str) != str(value)

    elif operator in ('>', '>=', '<', '<='):
        num_col = coerce_numeric_series(col_values)
        if numeric_val is None:
            numeric_val = 0
        if operator == '>':
            return num_col > numeric_val
        elif operator == '>=':
            return num_col >= numeric_val
        elif operator == '<':
            return num_col < numeric_val
        elif operator == '<=':
            return num_col <= numeric_val

    elif operator == 'contains':
        # SECURITY: regex=False (anti-ReDoS). Vedi commento in op_filter
        return col_values.astype(str).str.contains(str(value), case=False, na=False, regex=False)

    elif operator == 'startswith':
        return col_values.astype(str).str.startswith(str(value), na=False)

    elif operator == 'endswith':
        return col_values.astype(str).str.endswith(str(value), na=False)

    elif operator == 'between':
        parts = str(value).split(',')
        if len(parts) == 2:
            low = float(parts[0].strip())
            high = float(parts[1].strip())
            num_col = coerce_numeric_series(col_values)
            return (num_col >= low) & (num_col <= high)

    return false_boolean_series(col_values.index)
