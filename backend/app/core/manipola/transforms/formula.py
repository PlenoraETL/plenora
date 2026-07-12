"""
Cluster FORMULA — colonna calcolata via espressione utente con

gate AST anti-RCE.
"""

import ast as _ast

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.ports import FrameLike
from app.core.dataframes.tabular import (
    coerce_numeric_series,
    dataframe_eval_namespace,
    series_null_ratio,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

DataFrame = FrameLike


# COLONNA CALCOLATA (FORMULA) — Validazione AST anti-RCE

# Attributi pandas "safe" che le formule possono chiamare (es. col.str.upper
# Tutto ciò che NON è in questa whitelist viene rifiutato dall'AST validator
_SAFE_PANDAS_ATTRS = frozenset({
    # Series.str.*
    "str", "upper", "lower", "strip", "lstrip", "rstrip", "replace", "contains",
    "startswith", "endswith", "len", "split", "join", "cat", "slice", "get",
    "pad", "center", "zfill", "wrap", "extract", "findall", "match", "count",
    "title", "capitalize", "swapcase", "normalize", "isnumeric", "isalpha",
    "isdigit", "isspace", "isdecimal",
    # Series.dt.*
    "dt", "year", "month", "day", "hour", "minute", "second", "dayofweek",
    "date", "time", "normalize", "strftime", "floor", "ceil", "round",
    # Series methods comuni
    "fillna", "astype", "round", "abs", "clip", "map", "apply",
    "isnull", "notnull", "isna", "notna",
    # Pandas top-level
    "to_numeric", "to_datetime", "to_timedelta",
    "concat", "cut", "qcut",
})

# Nomi AST ammessi (whitelist positiva
# Ogni nodo che appare nell'AST di una formula deve essere in questo set
# altrimenti la formula viene rifiutata. Whitelist e' piu' sicura di
# blocklist: i nodi futuri/esotici (walrus, match-case, f-string con
# expression embed, comprehension con gadget chain, lambda, ecc.) sono
# rifiutati per default
# Include
# wrapper (Expression, Load
# riferimenti (Constant, Name, Attribute, keyword per kwargs
# chiamate (Call
# operatori (BinOp, UnaryOp, BoolOp, Compare
# tutti gli operator types (Add, Sub, Mult, ..., Eq, Lt, ..., And, Or, Not
# NON include (consapevolmente
# Lambda, FunctionDef, ClassDef, AsyncFunctionDef
# ListComp, SetComp, DictComp, GeneratorExp
# NamedExpr (walrus), JoinedStr/FormattedValue (f-string
# IfExp (ternary), Subscript, Slice
# List, Tuple, Set, mapping literal
# Import, ImportFrom, Global, Nonlocal, Delete, Yield, Await, Try
_ALLOWED_AST_NODES = frozenset({
    # Wrappers / context
    "Expression", "Load",
    # Valori e riferimenti
    "Constant", "Name", "Attribute", "keyword",
    # Chiamate
    "Call",
    # Operator kinds
    "BinOp", "UnaryOp", "BoolOp", "Compare",
    # BinOp operators
    "Add", "Sub", "Mult", "Div", "Mod", "Pow", "FloorDiv",
    "BitAnd", "BitOr", "BitXor", "LShift", "RShift", "MatMult",
    # UnaryOp operators
    "USub", "UAdd", "Not", "Invert",
    # BoolOp operators
    "And", "Or",
    # Compare operators (Is/IsNot/In/NotIn esclusi consapevolmente
    "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE",
})

# Nomi di attributo vietati: pattern di escape dal sandbox
_FORBIDDEN_ATTR_NAMES = frozenset({
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__init__", "__globals__", "__builtins__", "__import__",
    "__code__", "__func__", "__self__", "__module__",
    "__dict__", "__weakref__", "__reduce__", "__reduce_ex__",
    "__getattr__", "__setattr__", "__delattr__",
})

# Nomi builtin vietati come ast.Name (aggirerebbero __builtins__={}
# se l'utente avesse una colonna con lo stesso nome
_FORBIDDEN_NAMES = frozenset({
    "__import__", "__builtins__", "eval", "exec",
    "compile", "open", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "dir", "type",
    "breakpoint", "exit", "quit",
})


def _validate_formula_ast(formula: str) -> None:
    """Valida una formula utente via whitelist AST per prevenire code injection.

    Parsa la formula come espressione Python, poi attraversa l'albero AST
    richiedendo che ogni nodo sia in `_ALLOWED_AST_NODES`. Nodi sconosciuti
    (future syntax, costrutti esotici) vengono rifiutati per default.

    Su `Attribute` blocca anche accessi a dunder / attributi privati non
    whitelisted. Su `Name` blocca builtin pericolosi anche se passati come
    colonne.

    Solleva ValueError se la formula contiene costrutti non sicuri.
    Chiamata PRIMA di eval in op_formula.
    """
    try:
        tree = _ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Formula non valida (syntax error): {e}")

    for node in _ast.walk(tree):
        node_name = type(node).__name__

        # Whitelist: il nodo DEVE essere ammesso
        if node_name not in _ALLOWED_AST_NODES:
            raise ValueError(
                f"Formula non consentita: nodo AST '{node_name}' non in whitelist "
                f"(costrutti ammessi: aritmetica, confronti, method call su pandas)"
            )

        # Controlli aggiuntivi sul contenuto dei nodi ammessi
        if isinstance(node, _ast.Attribute):
            attr_name = node.attr
            if attr_name in _FORBIDDEN_ATTR_NAMES:
                raise ValueError(
                    f"Formula non consentita: accesso a '{attr_name}' non ammesso "
                    f"(possibile tentativo di escape dal sandbox)"
                )
            # Attributi che iniziano con _ ma non sono in whitelist pandas
            if attr_name.startswith("_") and attr_name not in _SAFE_PANDAS_ATTRS:
                raise ValueError(
                    f"Formula non consentita: attributo privato '{attr_name}' non ammesso"
                )
            # La whitelist deve essere applicata anche agli attributi
            # pubblici. Limitarsi a bloccare dunder/private lascia raggiungibile
            # qualunque oggetto esportato da pandas (per esempio
            # ``pd.io.common.os.system``), trasformando il fallback eval in
            # esecuzione di codice arbitrario sul worker.
            if attr_name not in _SAFE_PANDAS_ATTRS:
                raise ValueError(
                    f"Formula non consentita: attributo '{attr_name}' non ammesso"
                )

        if isinstance(node, _ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise ValueError(
                f"Formula non consentita: '{node.id}' non ammesso "
                f"(funzione pericolosa)"
            )


def _extract_referenced_names(formula: str) -> set[str]:
    """Ritorna il set di tutti i `Name` referenziati nell'AST della formula.

    Usato per limitare `local_vars` alle sole colonne citate, evitando
    la conversione numerica per-colonna su DataFrame
    wide. Fallback safe: se l'AST non parse-a, ritorna set vuoto e
    il caller cadra' sul comportamento tutto-colonne.

    Args:
        formula: stringa formula utente (gia' stripped).

    Returns:
        Set di nomi (str). Include built-in del namespace pandas eval
        (es. `to_numeric`, `concat`); il caller deve filtrare contro
        `df.columns` per scartare quelli non-colonna.
    """
    try:
        tree = _ast.parse(formula, mode="eval")
    except (SyntaxError, ValueError):
        return set()
    names: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name):
            names.add(node.id)
    return names


# COLONNA CALCOLATA (FORMULA
# Aggiunge colonna con espressione Python/Pandas valutata per riga

def op_formula(
    df: DataFrame,
    formula: str,
    new_column: str = "calculated"
) -> DataFrame:
    """
    Crea una nuova colonna calcolata con un'espressione.

    Strategia di valutazione (in ordine):
    1. pandas.eval — veloce, sicuro, gestisce operazioni vettoriali
    2. eval con namespace colonne — fallback per espressioni pandas.eval non regge
       Le colonne vengono passate come variabili locali, non sostituite nel testo.
       Le colonne object vengono auto-convertite a numerico dove possibile.

    Args:
        df: DataFrame input
        formula: Espressione da valutare (es. "col1 * col2", "'Prefisso: ' + nome")
        new_column: Nome della nuova colonna

    Returns:
        DataFrame con nuova colonna aggiunta, o df invariato se la formula è invalida.
    """
    if not formula or not formula.strip():
        logger.warning("Formula vuota")
        return df

    result = copy_frame_for_update(df)
    formula = formula.strip()

    # Defense-in-depth: scarta colonne con nome _-prefix/dunder prima di
    # esporle al motore eval (sia pandas.eval sia eval manuale). L'AST
    # validator blocca gia' i builtin pericolosi come ast.Name, ma evitiamo
    # comunque di esporre nomi strani nel namespace: se un attacker riuscisse
    # a iniettare una colonna chiamata come un builtin o un dunder, non
    # entrerebbe comunque nelle variabili dell'eval
    unsafe_cols = [c for c in result.columns if isinstance(c, str) and c.startswith("_")]
    if unsafe_cols:
        logger.warning(
            f"Formula: colonne {unsafe_cols} scartate dal namespace eval "
            f"(nomi _-prefix/dunder, defense-in-depth)"
        )
        eval_df = result.drop(columns=unsafe_cols)
    else:
        eval_df = result

    try:
        # SECURITY: validazione AST PRIMA di qualsiasi engine eval (pandas.eval
        # incluso). pandas.eval ha backend `numexpr`/`python` e in alcune
        # configurazioni puo' valutare attribute access / call: senza gate AST
        # davanti, un payload malicioso potrebbe sfuggire al solo controllo
        # del fallback path. Spostando il validator qui copriamo entrambi i
        # tentativi con la stessa whitelist
        _validate_formula_ast(formula)

        # Gestisce espressioni matematiche e booleane vettoriali
        # Non regge per: metodi stringa (.str.upper), concatenazioni con costanti stringa
        try:
            result[new_column] = eval_df.eval(formula)
            logger.info(f"Formula (pandas.eval) ok: '{new_column}'")
            return result
        except Exception as _pd_eval_err:
            logger.debug(
                f"pandas.eval fallita per '{new_column}', fallback a "
                f"eval() con namespace colonne: {_pd_eval_err}"
            )

        # Passa le colonne come variabili locali — nessuna sostituzione nel testo della formula
        # Auto-conversione a numerico solo per colonne referenziate dalla
        # formula: RAM proporzionale alla complessita' della formula, non alla
        # larghezza del DataFrame.
        referenced_names = _extract_referenced_names(formula)
        cols_to_prepare = [
            c for c in eval_df.columns
            if c in referenced_names
        ]
        local_vars = {}
        for col in cols_to_prepare:
            series = eval_df[col]
            if series.dtype == 'object':
                # Prova conversione a numerico (gestisce formato IT: 1.234,56
                converted = coerce_numeric_series(series)
                nan_ratio = series_null_ratio(converted)
                original_na = series_null_ratio(series)
                if nan_ratio - original_na > 0.3:
                    # Troppi NaN nuovi → prova formato italiano
                    # Single-pass regex: rimuove €$ e separatori migliaia, converte , in
                    cleaned = (
                        series.astype(str)
                        .str.strip()
                        .str.replace(r'[€$]', '', regex=True)
                        .str.replace('.', '', regex=False)  # separatore migliaia IT
                        .str.replace(',', '.', regex=False)  # decimale IT
                    )
                    converted = coerce_numeric_series(cleaned)
                # Se la conversione ha senso (pochi NaN aggiuntivi), usa numerica
                if nan_ratio - original_na <= 0.3:
                    local_vars[col] = converted
                else:
                    local_vars[col] = series  # mantieni stringa
            else:
                local_vars[col] = series

        # SECURITY: validazione AST gia' eseguita all'inizio del try (sopra
        # prima di qualsiasi engine eval. Niente da rifare qui
        result[new_column] = eval(
            formula,
            {"__builtins__": {}, **dataframe_eval_namespace()},
            local_vars,
        )
        logger.info(f"Formula (eval namespace) ok: '{new_column}'")
        return result

    # intent: fail-closed — formula utente non valida (sintassi, AST
    # bloccata per security, runtime error) solleva ValueError con
    # messaggio safe. Dettaglio tecnico preservato nel logger.warning
    # server-side. Security-win (audit §7.5): il blocco AST validator
    # ora e' visibile all'utente invece di silent "non ha effetto"
    except Exception as e:
        logger.warning(f"[GESTITO] Formula '{formula}' non valida: {e}")
        raise ValueError(
            f"Formula '{formula}' non valida o non consentita. "
            f"Verificare sintassi e operatori ammessi."
        ) from e
