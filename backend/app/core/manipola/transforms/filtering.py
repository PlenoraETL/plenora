"""
Cluster FILTERING — selezione righe e logica condizionale colonna.

Origine
=======
Estratto da `app.core.manipola.transforms.base` come parte di Phase 5
Batch B1.8 (cluster split, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1 facade pattern).

Razionale del raggruppamento: tutte le entita' qui dentro **valutano
condizioni su valori di colonna**:
  - `op_filter`           — riduce le righe del DataFrame in base a una
                            condizione singola (`==`, `>`, `contains`,
                            `isnull`, ecc.).
  - `op_conditional`      — crea una nuova colonna con valore dipendente
                            da N condizioni in cascata (case/when SQL).
  - `_evaluate_condition` — helper privato condiviso da `op_conditional`
                            per applicare la singola condizione su una
                            Series e ottenere la maschera booleana.

Tutti e 3 condividono semantica "condizione su Series → mask booleana".
Il private helper `_evaluate_condition` resta single-source qui:
nessun consumer esterno lo importa (verificato pre-extraction).

Contenuto:
  - `op_filter`           — riduzione righe fail-closed con auto-conversion
                            tipo + ReDoS-safe `contains` (regex=False).
  - `op_conditional`      — case/when su colonna sorgente con
                            auto-detection numerico sull'output.
  - `_evaluate_condition` — switch su 11 operatori, ritorna maschera
                            pandas; usato solo da `op_conditional`.

Invarianti pubbliche preservate (regola §6.1 zero call-site changes):
  - `from app.core.manipola.transforms.base import op_filter` /
    `op_conditional` continua a funzionare grazie al facade re-export
    in `base.py`.
  - `from app.core.manipola.transforms import op_filter` /
    `op_conditional` continua a funzionare (`__init__.py` re-exporta
    entrambi via `from .base import ...` che cattura il facade).
  - `transforms_registry.json` con entries `"base.op_filter"` /
    `"base.op_conditional"` continua a risolvere via
    `getattr(<base module>, "<func>")` dal `PluginExecutor`.
  - Nuovo path canonico `from app.core.manipola.transforms.filtering
    import <op>` disponibile per le 2 pubbliche.
  - `_evaluate_condition` NON e' re-exported da base (verificato:
    nessun consumer esterno). Resta single-source in filtering.

Logger naming
=============
Logger creato esplicitamente come
`"app.core.manipola.transforms.base"` (NON `__name__`) per preservare
il contratto osservabile pre-extraction: i record `Filter: ...`,
`[GESTITO] Errore Filter`, `Conditional: ...`, `[CRASH] Errore
Conditional` continuano ad apparire sotto il logger name dei
consumer. Stesso pattern di tutti i moduli figli estratti nei batch
B1.1-B1.7.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`base.py` al momento di B1.8. Nessuna modifica di logica, comment,
log payload, ordine di operazione, fail-closed semantics, anti-ReDoS
regex=False, type auto-conversion.
"""

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
# FILTRO RIGHE
# Applica condizioni su colonne con operatori: =, !=, >, <, >=, <=, LIKE, IN, IS NULL.
# =============================================================================

def op_filter(
    df: DataFrame,
    column: str,
    operator: str,
    value: Any = None
) -> DataFrame:
    """
    Filtra righe in base a una condizione.

    Args:
        df: DataFrame input
        column: Colonna da filtrare
        operator: Operatore (==, !=, >, >=, <, <=, contains, startswith, endswith, isnull, notnull)
        value: Valore da confrontare

    Returns:
        DataFrame filtrato
    """
    if column not in df.columns:
        # Fail-fast: un filtro che non filtra e' un bug grave. Silent return
        # df invariato nascondeva l'errore e i conteggi a valle erano sballati.
        available = list(df.columns)
        raise ValueError(
            f"Filter: colonna '{column}' non trovata. "
            f"Colonne disponibili: {available}"
        )

    result = df.copy()
    col = result[column]

    # === AUTO-CONVERSIONE TIPO ===
    # Se la colonna è numerica e il valore è stringa, converti
    if value is not None and pd.api.types.is_numeric_dtype(col):
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
            # FIX B-BE-MANIPOLA-04: value è già convertito dall'auto-conversione sopra
            # Se non convertibile, value rimane stringa → confronto fallisce → df invariato
            mask = col > value
        elif operator == '>=':
            mask = col >= value
        elif operator == '<':
            mask = col < value
        elif operator == '<=':
            mask = col <= value
        elif operator == 'contains':
            # SECURITY: regex=False tratta il valore come stringa letterale.
            # Prima: regex=True (default) → un utente poteva iniettare pattern
            # catastrofici come ((a+)+)+b causando ReDoS (CPU hang sul worker).
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
            return df

        result = result[mask].reset_index(drop=True)
        logger.info(f"Filter: {len(df)} -> {len(result)} righe")
        return result

    # intent: fail-closed — input utente invalido (operatore/valore
    # incompatibile con tipo colonna, runtime error pandas) solleva
    # ValueError con messaggio safe. str(e) tecnico preservato solo
    # nel logger.warning server-side per postmortem admin.
    except Exception as e:
        logger.warning(f"[GESTITO] Errore Filter '{operator}' su colonna '{column}': {e}")
        raise ValueError(
            f"Filter: operatore '{operator}' non applicabile sulla colonna "
            f"'{column}' con il valore fornito. Verificare tipo e sintassi."
        ) from e


# =============================================================================
# CONDIZIONALE (CASE WHEN)
# Crea colonna con valore dipendente da condizioni (simile a SQL CASE WHEN).
# =============================================================================

def op_conditional(
    df: DataFrame,
    column: str,
    conditions: List[Dict[str, Any]],
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

    result = df.copy()

    try:
        # Log diagnostico: valori unici della colonna sorgente
        unique_vals = result[column].dropna().unique()
        logger.info(f"Conditional: colonna '{column}' - {len(unique_vals)} valori unici, sample: {list(unique_vals[:10])}, dtype: {result[column].dtype}")

        # Inizializza con default
        result[output_column] = default_value

        col_values = result[column]

        # Applica condizioni in ordine inverso (la prima ha priorità)
        total_matched = 0
        for cond in reversed(conditions):
            op = cond.get('operator', '==')
            val = cond.get('value', '')
            res = cond.get('result', '')

            try:
                mask = _evaluate_condition(col_values, op, val)
                match_count = mask.sum()
                total_matched += match_count
                result.loc[mask, output_column] = res
                logger.info(f"Conditional:   {op} '{val}' -> '{res}' : {match_count} righe matchate")
            except Exception as e:
                logger.warning(f"Conditional: errore condizione {op} {val}: {e}")
                continue

        logger.info(f"Conditional: totale matchate={total_matched}, non matchate={len(result) - total_matched} (default='{default_value}')")

        # Auto-detect tipo numerico: se tutti i result values (e default)
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
            result[output_column] = pd.to_numeric(result[output_column], errors='coerce')
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
            return pd.to_numeric(col_values, errors='coerce') == numeric_val
        return col_values.astype(str) == str(value)

    elif operator == '!=':
        if numeric_val is not None:
            return pd.to_numeric(col_values, errors='coerce') != numeric_val
        return col_values.astype(str) != str(value)

    elif operator in ('>', '>=', '<', '<='):
        num_col = pd.to_numeric(col_values, errors='coerce')
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
        # SECURITY: regex=False (anti-ReDoS). Vedi commento in op_filter.
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
            num_col = pd.to_numeric(col_values, errors='coerce')
            return (num_col >= low) & (num_col <= high)

    return pd.Series(False, index=col_values.index)
