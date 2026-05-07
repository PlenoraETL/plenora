"""
Cluster ANALYSIS — operazioni di inspection/analisi del DataFrame.

Origine
=======
Estratto da `app.core.manipola.transforms.base` come parte di Phase 5
Batch B1.9 (cluster split, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1 facade pattern).

Razionale del nome `analysis.py` (vs `stats.py`)
=================================================
Il cluster contiene operazioni eterogenee per natura: statistiche
descrittive (`op_statistics`), discretizzazione numerica (`op_bin`),
mapping chiave→valore (`op_lookup`), espansione JSON (`op_flatten_json`),
campionamento (`op_sample`). Il filo conduttore non e' "stats" in
senso stretto ma piuttosto "operazioni di **analisi/inspection** del
dataset che producono colonne derivate o sottoinsiemi senza alterare
la semantica delle righe esistenti".

Contenuto:
  - `op_sample`        — campionamento casuale (n / fraction /
                         stratificato proporzionale).
  - `op_lookup`        — mappatura valori via dizionario chiave→valore.
  - `op_bin`           — discretizzazione numerica in N intervalli
                         (cut con bordi custom o auto).
  - `op_flatten_json`  — espansione colonna JSON/dict in colonne
                         separate via `pd.json_normalize`.
  - `op_statistics`    — statistiche descrittive (count/min/max/sum/
                         mean/median/std/var/q25/q75) globali o
                         per-gruppo (FME StatisticsCalculator equiv).

Invarianti pubbliche preservate (regola §6.1 zero call-site changes):
  - `from app.core.manipola.transforms.base import <op>` continua a
    funzionare per tutte e 5 grazie al facade re-export in `base.py`.
  - `from app.core.manipola.transforms import op_statistics` continua
    a funzionare (`__init__.py` re-exporta gia' `op_statistics` via
    `from .base import ...` che cattura il facade). Le altre 4
    (`op_sample`, `op_lookup`, `op_bin`, `op_flatten_json`) non sono
    esposte da init (verificato pre-extraction) → nessun cambio
    richiesto.
  - `transforms_registry.json` con entries `"base.op_*"` per tutte e 5
    continua a risolvere via `getattr(<base module>, "<func>")` dal
    `PluginExecutor`.
  - Nuovo path canonico `from app.core.manipola.transforms.analysis
    import <op>` disponibile.

Cleanup naturale (effetto del move)
====================================
`import json` rimosso da `base.py`: era usato esclusivamente da
`op_flatten_json` (verificato pre-extraction). Le altre occorrenze
del token `json` in `base.py` post-move sono in docstring/commenti
che citano il file `transforms_registry.json` come oggetto referenziato
(non sono import o consumer del modulo Python).

Logger naming
=============
Logger creato esplicitamente come
`"app.core.manipola.transforms.base"` (NON `__name__`) per preservare
il contratto osservabile pre-extraction: i record `Sample: ...`,
`[CRASH] Errore Sample`, `Lookup: ...`, `Bin: ...`, `FlattenJSON: ...`,
`Statistics completato` continuano ad apparire sotto il logger name
dei consumer. Stesso pattern di tutti i moduli figli estratti nei
batch B1.1-B1.8.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`base.py` al momento di B1.9. Nessuna modifica di logica, comment,
log payload, ordine di operazione, fail-closed semantics.
"""

import json

import pandas as pd
import geopandas as gpd
from typing import Any, Dict, List, Optional, Union

from app.utils.logger import get_logger

logger = get_logger("app.core.manipola.transforms.base")

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# CAMPIONAMENTO RIGHE
# Estrae un campione casuale di righe (per numero o per frazione).
# Supporta campionamento stratificato proporzionale.

def op_sample(
    df: DataFrame,
    n: int = 100,
    fraction: Optional[float] = None,
    random_state: Optional[int] = None,
    stratify_column: Optional[str] = None
) -> DataFrame:
    """
    Campionamento casuale di righe.

    Args:
        df: DataFrame input
        n: Numero di righe da campionare (ignorato se fraction è impostato)
        fraction: Frazione di righe (0.0-1.0), se impostato ha priorità su n
        random_state: Seed per riproducibilità
        stratify_column: Colonna per campionamento stratificato proporzionale

    Returns:
        DataFrame campionato
    """
    if len(df) == 0:
        return df

    try:
        result = df.copy()

        if stratify_column and stratify_column in result.columns:
            # Campionamento stratificato: proporzioni uguali da ogni gruppo
            groups = []
            for _, group in result.groupby(stratify_column):
                if fraction is not None:
                    group_n = max(1, int(len(group) * fraction))
                else:
                    # Proporzione del gruppo rispetto al totale
                    group_n = max(1, int(n * len(group) / max(len(result), 1)))

                group_n = min(group_n, len(group))
                groups.append(group.sample(n=group_n, random_state=random_state))

            result = pd.concat(groups, ignore_index=True)
        else:
            if fraction is not None:
                fraction = max(0.0, min(1.0, fraction))
                result = result.sample(frac=fraction, random_state=random_state).reset_index(drop=True)
            else:
                n = min(n, len(result))
                result = result.sample(n=n, random_state=random_state).reset_index(drop=True)

        logger.info(f"Sample: {len(df)} -> {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Sample: {e}", exc_info=True)
        raise


# LOOKUP — Mappatura valori tramite dizionario
# Sostituisce valori di una colonna secondo una mappa chiave→valore.

def op_lookup(
    df: DataFrame,
    column: str,
    mapping: Dict[str, Any],
    default: Any = None,
    output_column: Optional[str] = None
) -> DataFrame:
    """
    Mappa valori di una colonna tramite dizionario.

    Args:
        df: DataFrame input
        column: Colonna da mappare
        mapping: Dizionario chiave→valore per la sostituzione
        default: Valore per chiavi non trovate (None = mantieni originale)
        output_column: Colonna di output (None = sovrascrive la colonna sorgente)

    Returns:
        DataFrame con valori mappati
    """
    if column not in df.columns:
        logger.warning(f"Lookup: colonna '{column}' non trovata")
        return df

    if not mapping:
        logger.warning("Lookup: mapping vuoto, nessuna modifica")
        return df

    try:
        result = df.copy()
        target = output_column or column

        mapped = result[column].map(mapping)

        if default is not None:
            # Dove la mappa non ha trovato corrispondenza, usa default
            result[target] = mapped.fillna(default)
        else:
            # Dove la mappa non ha trovato corrispondenza, mantieni originale
            result[target] = mapped.fillna(result[column])

        mapped_count = result[column].isin(mapping.keys()).sum()
        logger.info(f"Lookup: {mapped_count}/{len(result)} valori mappati nella colonna '{target}'")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Lookup: {e}", exc_info=True)
        raise


# DISCRETIZZAZIONE (BINNING)
# Suddivide una colonna numerica in intervalli (bin) discreti.

def op_bin(
    df: DataFrame,
    column: str,
    bins: Any = 5,
    labels: Optional[List[str]] = None,
    output_column: Optional[str] = None
) -> DataFrame:
    """
    Discretizza una colonna numerica in intervalli.

    Args:
        df: DataFrame input
        column: Colonna numerica da discretizzare
        bins: Numero di bin (int) o lista di bordi (es. [0, 10, 50, 100])
        labels: Etichette personalizzate per i bin (lunghezza = numero bin)
        output_column: Colonna di output (None = "{column}_bin")

    Returns:
        DataFrame con colonna discretizzata
    """
    if column not in df.columns:
        logger.warning(f"Bin: colonna '{column}' non trovata")
        return df

    try:
        result = df.copy()
        target = output_column or f"{column}_bin"

        # Converti a numerico se necessario
        numeric_col = pd.to_numeric(result[column], errors='coerce')

        result[target] = pd.cut(
            numeric_col,
            bins=bins,
            labels=labels,
            include_lowest=True
        )

        n_bins = len(labels) if labels else (len(bins) - 1 if isinstance(bins, list) else bins)
        logger.info(f"Bin: colonna '{column}' discretizzata in {n_bins} intervalli -> '{target}'")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Bin: {e}", exc_info=True)
        raise


# FLATTEN JSON
# Espande una colonna contenente JSON/dict in colonne separate.

def op_flatten_json(
    df: DataFrame,
    column: str,
    prefix: str = "",
    max_level: int = 1
) -> DataFrame:
    """
    Espande una colonna JSON/dict in colonne separate.

    Supporta sia colonne con dict Python sia colonne con stringhe JSON.

    Args:
        df: DataFrame input
        column: Colonna contenente JSON/dict
        prefix: Prefisso per le colonne generate
        max_level: Livello massimo di nidificazione da espandere

    Returns:
        DataFrame con colonne JSON espanse
    """
    if column not in df.columns:
        logger.warning(f"FlattenJSON: colonna '{column}' non trovata")
        return df

    try:
        result = df.copy()

        # Parse stringhe JSON in dict
        def parse_json(val):
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    return parsed if isinstance(parsed, dict) else {}
                except (json.JSONDecodeError, TypeError):
                    return {}
            return {}

        parsed_col = result[column].apply(parse_json)

        # Normalizza in DataFrame piatto
        flat = pd.json_normalize(parsed_col.tolist(), max_level=max_level)

        if flat.empty:
            logger.warning(f"FlattenJSON: nessun dato JSON valido in '{column}'")
            return df

        # Applica prefisso alle colonne
        col_prefix = prefix if prefix else f"{column}_"
        flat.columns = [f"{col_prefix}{c}" for c in flat.columns]

        # Allinea indici e unisci
        flat.index = result.index
        result = pd.concat([result, flat], axis=1)

        logger.info(f"FlattenJSON: {len(flat.columns)} colonne estratte da '{column}'")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore FlattenJSON: {e}", exc_info=True)
        raise


# STATISTICS — StatisticsCalculator
# Calcola statistiche descrittive per colonna, con raggruppamento opzionale.

def op_statistics(
    df: DataFrame,
    column: str,
    group_by: str = None,
    stats: List[str] = None,
    output_prefix: str = ""
) -> DataFrame:
    """
    Calcola statistiche descrittive su una colonna numerica,
    opzionalmente raggruppate per un'altra colonna.

    Equivalente FME: StatisticsCalculator.

    Le statistiche vengono aggiunte come nuove colonne al DataFrame
    originale. Se group_by è specificato, ogni riga ottiene le
    statistiche del proprio gruppo.

    Args:
        df: DataFrame input
        column: Colonna numerica su cui calcolare le statistiche
        group_by: Colonna per raggruppamento (opzionale)
        stats: Lista di statistiche da calcolare. Default:
               ["count", "min", "max", "mean", "median", "std"].
               Valori supportati: count, min, max, sum, mean, median,
               std, var, q25, q75
        output_prefix: Prefisso per le colonne output.
                       Se vuoto, usa "{column}_"

    Returns:
        DataFrame con colonne statistiche aggiunte
    """
    if column not in df.columns:
        logger.warning(f"Statistics: colonna '{column}' non trovata")
        return df

    if stats is None:
        stats = ["count", "min", "max", "mean", "median", "std"]

    # Mappa nomi stat -> funzioni pandas
    stat_funcs = {
        "count": "count",
        "min": "min",
        "max": "max",
        "sum": "sum",
        "mean": "mean",
        "median": "median",
        "std": "std",
        "var": "var",
        "q25": lambda x: x.quantile(0.25),
        "q75": lambda x: x.quantile(0.75),
    }

    prefix = output_prefix if output_prefix else f"{column}_"

    try:
        result = df.copy()

        if group_by and group_by in df.columns:
            # Statistiche per gruppo
            agg_dict = {}
            for stat_name in stats:
                if stat_name in stat_funcs:
                    agg_dict[f"{prefix}{stat_name}"] = (column, stat_funcs[stat_name])

            # Calcola con NamedAgg
            grouped = df.groupby(group_by, dropna=False).agg(**agg_dict)
            result = result.merge(grouped, on=group_by, how="left")

        else:
            # Statistiche globali — aggiungi come colonna costante
            series = df[column]
            for stat_name in stats:
                col_name = f"{prefix}{stat_name}"
                func = stat_funcs.get(stat_name)
                if func is None:
                    continue
                if callable(func):
                    result[col_name] = func(series)
                else:
                    result[col_name] = getattr(series, func)()

        computed = [s for s in stats if s in stat_funcs]
        logger.info(
            f"Statistics completato: {len(computed)} statistiche su '{column}'"
            + (f" raggruppate per '{group_by}'" if group_by else "")
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Statistics: {e}", exc_info=True)
        raise
