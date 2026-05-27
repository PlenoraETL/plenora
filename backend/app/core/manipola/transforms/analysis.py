"""
Cluster ANALYSIS — operazioni di inspection/analisi del DataFrame.

Keeps transformation behavior isolated from orchestration callers.
"""

import json

from typing import Any, List, Optional

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.ports import FrameLike
from app.core.dataframes.tabular import (
    concat_dataframes,
    cut_numeric_series,
    dataframe_from_mapping,
    group_named_aggregate_merge,
    iter_grouped_dataframes,
    normalize_json_records,
    sample_dataframe,
    sample_rows_reset_index,
)
from app.core.manipola.types import FrameMap, JsonObject
from app.utils.logger import get_logger

logger = get_logger("app.core.manipola.transforms.base")

DataFrame = FrameLike


# CAMPIONAMENTO RIGHE
# Estrae un campione casuale di righe (per numero o per frazione
# Supporta campionamento stratificato proporzionale

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
        # Pre-fix `df.copy()` non serviva: tutto il path costruisce
        # `result` ex-novo (sample_rows_reset_index / concat_dataframes
        # ritornano nuove istanze) e l'input non viene mai mutato.
        if stratify_column and stratify_column in df.columns:
            # Campionamento stratificato: proporzioni uguali da ogni gruppo
            groups = []
            for _, group in iter_grouped_dataframes(df, stratify_column):
                if fraction is not None:
                    group_n = max(1, int(len(group) * fraction))
                else:
                    # Proporzione del gruppo rispetto al totale
                    group_n = max(1, int(n * len(group) / max(len(df), 1)))

                group_n = min(group_n, len(group))
                groups.append(sample_dataframe(group, n=group_n, random_state=random_state))

            result = concat_dataframes(groups, ignore_index=True)
        else:
            if fraction is not None:
                fraction = max(0.0, min(1.0, fraction))
                result = sample_rows_reset_index(
                    df,
                    frac=fraction,
                    random_state=random_state,
                )
            else:
                n = min(n, len(df))
                result = sample_rows_reset_index(
                    df,
                    n=n,
                    random_state=random_state,
                )

        logger.info(f"Sample: {len(df)} -> {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Sample: {e}", exc_info=True)
        raise


# LOOKUP — Mappatura valori tramite dizionario
# Sostituisce valori di una colonna secondo una mappa chiave→valore

def op_lookup(
    df: DataFrame,
    column: str,
    mapping: JsonObject,
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
        result = copy_frame_for_update(df)
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


# DISCRETIZZAZIONE (BINNING
# Suddivide una colonna numerica in intervalli (bin) discreti

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
        result = copy_frame_for_update(df)
        target = output_column or f"{column}_bin"

        result[target] = cut_numeric_series(
            result[column],
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
# Espande una colonna contenente JSON/dict in colonne separate

def op_flatten_json(
    df: DataFrame,
    column: str,
    prefix: str = "",
    max_level: int = 1,
    output_columns: Optional[List[str]] = None,
) -> DataFrame:
    """
    Espande una colonna JSON/dict in colonne separate.

    Supporta sia colonne con dict Python sia colonne con stringhe JSON.

    Args:
        df: DataFrame input
        column: Colonna contenente JSON/dict
        prefix: Prefisso per le colonne generate
        max_level: Livello massimo di nidificazione da espandere
        output_columns: Whitelist opzionale di chiavi JSON da estrarre
            (post-prefix). Vuoto = estrai tutte. Il parametro arriva dal
            registry: senza questo arg, il pipeline crashava con
            ``TypeError: unexpected keyword argument 'output_columns'``
            su qualunque nodo configurato dal UI.

    Returns:
        DataFrame con colonne JSON espanse
    """
    if column not in df.columns:
        logger.warning(f"FlattenJSON: colonna '{column}' non trovata")
        return df

    try:
        result = copy_frame_for_update(df)

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
        col_prefix = prefix if prefix else f"{column}_"

        # MANIPOLA-2 (2026-05-26): fast-path quando output_columns e' valorizzato.
        # Pre-fix: normalize_json_records globale -> 1 DataFrame con TUTTE le
        # chiavi (anche centinaia se i JSON sono pesanti), poi filter su
        # output_columns. Peak = full normalized DF + lista dict intermedia.
        # Post-fix: per ogni path richiesto, estrai via `.apply(dig)` come
        # singola Series, evitando json_normalize globale. RAM ~ len(paths)
        # x len(rows) invece di full schema discovery.
        #
        # M-2 follow-up: il fast-path RISPETTA `max_level` come il legacy
        # `json_normalize(..., max_level=max_level)`. Path piu' profondi
        # del cap vengono scartati con log warning (UI contract: l'utente
        # ha esplicitamente impostato max_level, le request di estrarre
        # path piu' deep sono coerenti col legacy che li tagliava).
        if output_columns:
            wanted = list(output_columns)
            # Strip del prefix per ottenere il path JSON originale.
            extracted_cols: FrameMap = {}
            skipped_too_deep: list = []
            for full_col_name in wanted:
                if not full_col_name.startswith(col_prefix):
                    continue
                json_path = full_col_name[len(col_prefix):]
                path_parts = json_path.split(".")
                # max_level rispecchia il legacy json_normalize: 0 = solo
                # top-level, 1 = un livello di nesting, ecc. Un path con
                # `len(parts) > max_level + 1` e' piu' profondo del cap.
                if max_level is not None and max_level >= 0 and len(path_parts) > max_level + 1:
                    skipped_too_deep.append(full_col_name)
                    continue

                def _dig(d, parts=path_parts):
                    cur = d
                    for p in parts:
                        if not isinstance(cur, dict):
                            return None
                        cur = cur.get(p)
                        if cur is None:
                            return None
                    return cur

                extracted_cols[full_col_name] = parsed_col.apply(_dig)

            if skipped_too_deep:
                logger.warning(
                    f"FlattenJSON fast-path: path piu' profondi di max_level={max_level} "
                    f"saranno scartati per coerenza con legacy json_normalize: "
                    f"{skipped_too_deep}"
                )

            if not extracted_cols:
                logger.warning(
                    f"FlattenJSON: output_columns {output_columns!r} non "
                    f"matchano nessun path estraibile da '{column}' "
                    f"(verifica prefix='{col_prefix}' e max_level={max_level})"
                )
                return df

            flat = dataframe_from_mapping(extracted_cols, index=result.index)
        else:
            # Path full: normalize_json_records scopre tutte le chiavi.
            flat = normalize_json_records(parsed_col.tolist(), max_level=max_level)

            if flat.empty:
                logger.warning(f"FlattenJSON: nessun dato JSON valido in '{column}'")
                return df

            flat.columns = [f"{col_prefix}{c}" for c in flat.columns]

        # Allinea indici e unisci
        flat.index = result.index
        result = concat_dataframes([result, flat], axis=1)

        logger.info(f"FlattenJSON: {len(flat.columns)} colonne estratte da '{column}'")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore FlattenJSON: {e}", exc_info=True)
        raise


# STATISTICS — StatisticsCalculator
# Calcola statistiche descrittive per colonna, con raggruppamento opzionale

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
        result = copy_frame_for_update(df)

        if group_by and group_by in df.columns:
            # Statistiche per gruppo
            agg_dict = {}
            for stat_name in stats:
                if stat_name in stat_funcs:
                    agg_dict[f"{prefix}{stat_name}"] = (column, stat_funcs[stat_name])

            # Calcola con NamedAgg
            result = group_named_aggregate_merge(
                result,
                group_by,
                agg_dict,
                how="left",
                dropna=False,
            )

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
