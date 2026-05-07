"""
Cluster CLEANSING — pulizia dati e coercion di tipo per colonna.

Keeps transformation behavior isolated from orchestration callers.
"""

import pandas as pd
import geopandas as gpd
from typing import Any, Union

from app.utils.logger import get_logger

logger = get_logger("app.core.manipola.transforms.base")

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# RIEMPIMENTO VALORI NULLI
# Riempie NULL con: valore costante, media, mediana, moda, ffill, bfill.

def _is_strict_numeric_series(s: "pd.Series") -> bool:
    """True se la Series ha dtype numerico (int/float) ma NON booleano.

    `pd.api.types.is_numeric_dtype` ritorna True anche su bool: lo
    escludiamo perche' una colonna boolean riempita con 0/1 e' un caso
    di tipo a parte (non vogliamo upcast bool->int silenzioso ne
    ValueError su `value=False`).
    """
    return (
        pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)
    )


def _coerce_fill_value_for_numeric(value: Any, col_name: str, dtype: Any) -> Any:
    """Valida un fill `value` contro una colonna numerica.

    Policy C2 Phase 4 (no upcast a object silenzioso):
      - None / NaN / numerici (int, float esclusi bool) -> accept as-is.
      - Stringa numericamente coercibile ("0", "1.5", "-3e2") -> coerce.
      - Tutto il resto (bool, "N/A", "", date string, dict, ...) -> ValueError.

    Bool e' rifiutato esplicitamente: `True/False` su colonna numerica e'
    quasi sempre un bug del canvas (fill flag invece che fill numero); il
    cast 0/1 silenzioso nasconderebbe la confusione.
    """
    # None: pandas semantics consentono None su colonna float (diventa NaN).
    if value is None:
        return value
    # NaN float: idem, nessun upcast.
    if isinstance(value, float) and pd.isna(value):
        return value
    # Bool e' un sottotipo di int in Python: gestiamolo PRIMA del check int.
    if isinstance(value, bool):
        raise ValueError(
            f"op_fill_na: colonna numerica '{col_name}' (dtype={dtype}) "
            f"non puo' essere riempita con valore booleano {value!r}: "
            f"fornire un valore numerico (int/float) o una stringa "
            f"numericamente coercibile (es. '0', '1.5')."
        )
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        # Stringa vuota e whitespace-only NON sono coercibili: rifiutali.
        # Senza questo check pd.to_numeric("") solleverebbe (gia' coperto),
        # ma vogliamo un messaggio chiaro che cita anche il fix tipico.
        if not value.strip():
            raise ValueError(
                f"op_fill_na: colonna numerica '{col_name}' (dtype={dtype}) "
                f"non puo' essere riempita con stringa vuota: il fillna "
                f"forzerebbe l'upcast a object, perdendo il dtype numerico. "
                f"Fornire un numero (es. 0) o omettere il fill su questa "
                f"colonna."
            )
        try:
            coerced = pd.to_numeric(value)
        except (ValueError, TypeError):
            raise ValueError(
                f"op_fill_na: colonna numerica '{col_name}' (dtype={dtype}) "
                f"non puo' essere riempita con valore non-numerico {value!r}: "
                f"il fillna upcast la colonna a object/testo, perdendo "
                f"semantica numerica downstream. Fornire un numero (es. 0) "
                f"o una stringa numericamente coercibile (es. '0', '1.5')."
            )
        # pd.to_numeric su scalar puo' ritornare numpy scalar: estrai .item()
        # per consistenza tipo-Python (float/int nativo).
        return coerced.item() if hasattr(coerced, "item") else coerced
    # Dict, list, tuple, datetime, ecc.: niente upcast accettabile.
    raise ValueError(
        f"op_fill_na: colonna numerica '{col_name}' (dtype={dtype}) "
        f"non puo' essere riempita con valore di tipo {type(value).__name__} "
        f"({value!r}): fornire un numero o una stringa numericamente "
        f"coercibile."
    )


def op_fill_na(
    df: DataFrame,
    column: str = None,
    method: str = "value",
    value: Any = ""
) -> DataFrame:
    """
    Riempie valori nulli.

    Args:
        df: DataFrame input
        column: Colonna specifica (None = tutte)
        method: 'value', 'ffill', o 'bfill'
        value: Valore da usare se method='value'

    Returns:
        DataFrame con valori nulli riempiti

    Raises:
        ValueError: se `method='value'` e `value` non e' compatibile con
            il dtype numerico di una colonna target. Senza questo guard
            (Phase 4 C2), `fillna("N/A")` su colonna float demoteva
            silenziosamente la colonna a object/testo, rompendo le
            aggregazioni e i join numerici a valle.
    """
    result = df.copy()

    try:
        if method in ['ffill', 'bfill']:
            # FIX: fillna(method=) è deprecato in pandas 2.0+.
            # Usare ffill()/bfill() direttamente.
            fill_func = 'ffill' if method == 'ffill' else 'bfill'
            if column and column in result.columns:
                result[column] = getattr(result[column], fill_func)()
            else:
                result = getattr(result, fill_func)()
        else:
            target_cols = (
                [column] if (column and column in result.columns)
                else list(result.columns)
            )
            for col in target_cols:
                s = result[col]
                if _is_strict_numeric_series(s):
                    fill_for_col = _coerce_fill_value_for_numeric(
                        value, col_name=col, dtype=s.dtype,
                    )
                else:
                    fill_for_col = value
                result[col] = s.fillna(fill_for_col)

        logger.info(f"Fill NA: column={column}, method={method}, value={value}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Fill NA: {e}", exc_info=True)
        raise


# SOSTITUZIONE VALORI
# Sostituisce valori in colonna. Supporta regex opzionale.

def op_replace(
    df: DataFrame,
    column: str,
    old_value: str,
    new_value: str,
    regex: bool = False
) -> DataFrame:
    """
    Sostituisce valori in una colonna.

    Args:
        df: DataFrame input
        column: Colonna target
        old_value: Valore da cercare
        new_value: Valore sostitutivo
        regex: Se True, interpreta old_value come regex

    Returns:
        DataFrame con valori sostituiti
    """
    if not column or column not in df.columns:
        return df

    result = df.copy()

    try:
        if regex:
            result[column] = result[column].astype(str).str.replace(
                str(old_value), str(new_value), regex=True
            )
        else:
            result[column] = result[column].replace(old_value, new_value)

        logger.info(f"Replace: {column} '{old_value}' -> '{new_value}'")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Replace: {e}", exc_info=True)
        raise


# CONVERSIONE TIPI
# Converte colonne tra tipi: int, float, str, bool, date/datetime.

def op_type_cast(
    df: DataFrame,
    column: str,
    target_type: str = "str",
    date_format: str = "",
    errors: str = "coerce"
) -> DataFrame:
    """
    Converte il tipo di una colonna.

    Args:
        df: DataFrame input
        column: Colonna da convertire
        target_type: Tipo destinazione (str, int, float, bool, date, datetime)
        date_format: Formato data per parsing (es: %d/%m/%Y). Vuoto = auto-detect
        errors: Gestione errori (coerce=NULL, ignore=mantieni, raise=blocca)

    Returns:
        DataFrame con colonna convertita
    """
    if not column or column not in df.columns:
        logger.warning(f"Type Cast: colonna '{column}' non trovata")
        return df

    result = df.copy()

    try:
        if target_type == 'str':
            result[column] = result[column].astype(str)
            # Pulisci 'None' e 'nan' in stringhe vuote
            result[column] = result[column].replace({'None': '', 'nan': '', 'NaT': ''})

        elif target_type == 'int':
            result[column] = pd.to_numeric(result[column], errors=errors)
            if errors == 'coerce':
                # Converti a Int64 (nullable) per preservare NULL
                result[column] = result[column].astype('Int64')
            else:
                result[column] = result[column].astype(int)

        elif target_type == 'float':
            # Gestisci virgola italiana come separatore decimale
            if result[column].dtype == object:
                result[column] = result[column].astype(str).str.replace(',', '.', regex=False)
            result[column] = pd.to_numeric(result[column], errors=errors)

        elif target_type == 'bool':
            truthy = {'true', '1', 'yes', 'si', 'sì', 'vero', 't', 'y', 's'}
            result[column] = result[column].astype(str).str.strip().str.lower().isin(truthy)

        elif target_type in ('date', 'datetime'):
            if date_format and date_format.strip():
                result[column] = pd.to_datetime(
                    result[column], format=date_format, errors=errors
                )
            else:
                # Auto-detect: prova prima formato italiano, poi ISO
                result[column] = pd.to_datetime(
                    result[column], dayfirst=True, errors=errors
                )

            if target_type == 'date':
                result[column] = result[column].dt.date

        logger.info(f"Type Cast: {column} -> {target_type}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Type Cast '{column}' -> {target_type}: {e}", exc_info=True)
        raise
