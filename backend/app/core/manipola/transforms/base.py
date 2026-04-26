"""
Operazioni di trasformazione base (non-geospaziali) per MANIPOLA.

Architettura
============
Questo modulo contiene tutte le funzioni di trasformazione non-geospaziali
registrate nel transforms_registry.json. Ogni funzione è una operazione
atomica che riceve un DataFrame (o lista di DataFrame) e restituisce
un DataFrame trasformato.

Convenzione funzioni:
- Input singolo: op_xxx(df: DataFrame, **kwargs) -> DataFrame
- Input multiplo: op_xxx(dfs: List[DataFrame], **kwargs) -> DataFrame
- Tutti i parametri arrivano come kwargs espliciti (preparati da PluginExecutor)
- Le funzioni NON modificano l'input: lavorano sempre su copie

Catalogo operazioni (~25):
- JOIN: op_join (inner/left/right/full/cross join tra due tabelle)
- FILTRO: op_filter (condizioni su colonne con operatori =, !=, >, <, LIKE, IN, etc.)
- SELEZIONA: op_select_columns (selezione/riordino colonne)
- RINOMINA: op_rename (rinomina colonne)
- CAST: op_cast_type (conversione tipi: int, float, str, bool, date)
- CALCOLA: op_add_column (colonna calcolata con espressioni Python/Pandas)
- CONCAT: op_concat (unione verticale di più DataFrame con allineamento colonne)
- DEDUP: op_deduplicate (rimozione duplicati su colonne selezionate)
- ORDINA: op_sort (ordinamento multi-colonna asc/desc)
- CAMPIONA: op_sample (campionamento casuale o primi N record)
- PIVOT: op_pivot (tabella pivot con indice, colonne, valori, aggregazione)
- MELT: op_melt (unpivot: da colonne a righe)
- SPLIT: op_split_column (divide colonna su separatore)
- MERGE: op_merge_columns (unisce colonne con separatore)
- FILL NULL: op_fill_null (riempimento valori nulli: costante, media, mediana, moda, ffill, bfill)
- REPLACE: op_replace_values (sostituzione valori con regex opzionale)
- GROUP BY: op_group_by (aggregazione con funzioni: count, sum, avg, min, max, first, last)
- COLONNA STATICA: op_add_static_column (aggiunge colonna con valore costante)
- GEOMETRY TO COLUMNS: op_geometry_to_columns (estrae lat/lon/wkt da geometria)
- COLUMNS TO GEOMETRY: op_columns_to_geometry (crea geometria da lat/lon)

Integrazione con PluginExecutor:
Ogni funzione viene importata dinamicamente tramite il path nel registry JSON.
Es: "base.op_join" → app.core.manipola.transforms.base.op_join

Uso diretto (per test):
    from app.core.manipola.transforms.base import op_join
    result = op_join([df1, df2], left_column="id", right_column="id", join_type="inner")
"""

import pandas as pd
import geopandas as gpd
from typing import Union

from app.utils.logger import get_logger

# Cleanup imports B1.11 (Phase 5 closeout):
#   - `import uuid` rimosso (era usato solo da op_uuid_generator,
#     extracted in B1.3c — oversight residuale chiuso ora).
#   - `import numpy as np` rimosso (era usato solo da op_md5_hash,
#     extracted in B1.11).
#   - `List, Dict, Any, Optional` rimossi da `typing` (erano usati
#     solo nelle signature delle funzioni; tutte le funzioni con
#     body sono extracted post-B1.11).
# Restano: `pd`, `gpd`, `Union` per il tipo alias `DataFrame`;
# `get_logger` + `logger` mantenuti come scheletro modulo (no
# function body, ma future-proof + symmetry con altri transforms.*).
logger = get_logger(__name__)

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# =============================================================================
# JOIN — Unione tabelle per colonna chiave
# Supporta: inner, left, right, outer, cross. Gestisce suffissi per colonne omonime.
# =============================================================================

# Phase 5 B1.1b + B1.2 cutover: cluster JOINS spostato in
# `app.core.manipola.transforms.joins`. Re-export per preservare i path
# pubblici esistenti:
#   - `from app.core.manipola.transforms.base import op_join`
#       (idem op_union, op_cross_join)
#   - `from app.core.manipola.transforms import op_join` (via __init__)
#   - `transforms_registry.json` entries "base.op_join" / "base.op_union"
#     / "base.op_cross_join" risolte da plugin_executor via
#     `getattr(<base module>, "<func>")`.
# Single-source canonica ora vive in `joins.py`. Vedi anche
# `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1 facade pattern.
from .joins import op_join, op_union, op_cross_join  # noqa: F401  (re-export, consumato via getattr)

# Alias storico per retrocompatibilità: `op_concat` = `op_union`.
# Re-bind dopo l'import facade per mantenere il nome importabile da
# `app.core.manipola.transforms.base` (consumer noto: __init__.py
# `from .base import op_union as op_concat`).
op_concat = op_union


# Phase 5 B1.8 cutover: op_filter moved to `transforms.filtering`
# (re-exported in testa al file via facade).


# Phase 5 B1.5 cutover: cluster FORMULA (op_formula + _validate_formula_ast
# + AST whitelists + `import ast as _ast` locale) spostato in
# `app.core.manipola.transforms.formula`. Re-export in testa al file via
# facade. `import ast as _ast` rimosso da base.py: era usato esclusivamente
# da questo cluster, no consumer altrove.


# Phase 5 B1.4 cutover: op_concat_columns moved to `transforms.columns`
# (re-exported in testa al file via facade).


# Phase 5 B1.7 cutover: op_aggregate + op_sort + op_distinct moved to
# `transforms.aggregation` (re-exported in testa al file via facade).


# Phase 5 B1.4 cutover: op_rename + op_drop_columns moved to
# `transforms.columns` (re-exported in testa al file via facade).


# Phase 5 B1.6 cutover: op_fill_na (+ helper privati
# _is_strict_numeric_series e _coerce_fill_value_for_numeric)
# e op_replace moved to `transforms.cleansing` (re-exported in
# testa al file via facade). I 2 helper privati restano single-source
# in `cleansing.py`: nessun consumer esterno li importa (verificato).


# Phase 5 B1.4 cutover: op_split_column moved to `transforms.columns`
# (re-exported in testa al file via facade).


# Phase 5 B1.11 cutover: cluster SECURITY (op_md5_hash + op_mask_data
# + 5 helper privati `_mask_*` + dict `MASK_FUNCTIONS`) spostato in
# `app.core.manipola.transforms.security`. Re-export in coda al file
# via facade. I 5 helper privati restano single-source nel nuovo
# modulo (verificato pre-extraction: zero consumer esterni).
#
# IMPORTANTE — patch.dict identita' oggetto:
# `MASK_FUNCTIONS` e' re-exported da `base.py` via `from .security
# import MASK_FUNCTIONS` (NON ribindato): il test contract
# `tests2/manipola/unit/test_op_mask_data_fail_closed.py:69,110`
# patcha `"app.core.manipola.transforms.base.MASK_FUNCTIONS"` con
# `patch.dict` (mutazione in-place). Poiche' base e security puntano
# allo stesso dict object, la mutazione e' visibile da `op_mask_data`
# (in security.py) che legge il dict via module-level lookup. Vedi
# docstring `transforms/security.py` sezione "Coupling".


# Phase 5 B1.6 cutover: op_type_cast moved to `transforms.cleansing`
# (re-exported in testa al file via facade).


# Phase 5 B1.11 cutover: op_geo_from_coords moved to `transforms.geo`
# (re-exported in coda al file via facade). Singleton self-contained
# senza helper privati ne' coupling. Imports `Point`/`wkt` restano
# function-local (lazy) preservati bit-per-bit.


# Phase 5 B1.8 cutover: op_conditional + _evaluate_condition (helper
# privato, NON re-exported) moved to `transforms.filtering` (re-exported
# in testa al file via facade per la sola op_conditional).


# Phase 5 B1.3b cutover: op_text_normalize moved to `transforms.strings`
# (re-exported in testa al file via facade).


# Phase 5 B1.10 cutover: op_pivot moved to `transforms.reshape`
# (re-exported in testa al file via facade).


# Phase 5 B1.10 cutover: op_table_diff moved to transforms.reshape
# (re-exported in testa al file via facade).


# Phase 5 B1.9 cutover: op_sample moved to `transforms.analysis`
# (re-exported in testa al file via facade).


# Phase 5 B1.10 cutover: op_melt moved to transforms.reshape
# (re-exported in testa al file via facade).
# Phase 5 B1.3c cutover: op_add_row_number + op_date_extract moved to
# `transforms.utility` (re-exported in testa al file via facade).


# =============================================================================
# LOOKUP / BINNING / FLATTEN JSON / STATISTICS
# Phase 5 B1.9 cutover: cluster ANALYSIS (op_lookup, op_bin,
# op_flatten_json, op_statistics) moved to `transforms.analysis`
# (re-exported in testa al file via facade).
# =============================================================================


# Phase 5 B1.3b cutover: op_string_extract moved to `transforms.strings`
# (re-exported in testa al file via facade).


# Phase 5 B1.2 cutover: op_cross_join moved to `transforms.joins`
# (re-exported in testa al file via facade). Sezione lasciata vuota
# per preservare l'ancoraggio storico delle linee successive.


# Phase 5 B1.10 cutover: op_transpose moved to transforms.reshape
# (re-exported in testa al file via facade).
# Phase 5 B1.4 cutover: op_reorder_columns moved to `transforms.columns`
# (re-exported in testa al file via facade).


# Phase 5 B1.3a + B1.3b cutover: cluster STRINGS spostato in
# `app.core.manipola.transforms.strings`. Re-export per preservare i
# path pubblici esistenti:
#   - `from app.core.manipola.transforms.base import op_string_pad`
#       (idem op_string_length, op_string_extract, op_text_normalize)
#   - `from app.core.manipola.transforms import op_string_extract`
#     (idem op_text_normalize) — `__init__.py` resta `from .base import
#     op_string_extract, op_text_normalize` e cattura il facade.
#   - `transforms_registry.json` entries "base.op_string_*" /
#     "base.op_text_normalize" risolte via `getattr(<base>, "<func>")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .strings import (  # noqa: F401  (re-export)
    op_string_pad,
    op_string_length,
    op_string_extract,
    op_text_normalize,
)

# Phase 5 B1.3c cutover: cluster UTILITY (op_add_row_number,
# op_date_extract, op_uuid_generator) spostato in
# `app.core.manipola.transforms.utility`. Re-export per preservare i
# path pubblici esistenti:
#   - `from app.core.manipola.transforms.base import <op>`
#   - `transforms_registry.json` entries "base.op_add_row_number" /
#     "base.op_date_extract" / "base.op_uuid_generator" risolte via
#     `getattr(<base module>, "<func>")`.
# Nessuna delle 3 e' esposta da `transforms/__init__.py` (verificato
# pre-extraction) → nessun cambio init richiesto.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .utility import (  # noqa: F401  (re-export)
    op_add_row_number,
    op_date_extract,
    op_uuid_generator,
)

# Phase 5 B1.4 cutover: cluster COLUMNS (op_drop_columns, op_rename,
# op_reorder_columns, op_concat_columns, op_split_column) spostato in
# `app.core.manipola.transforms.columns`. Approvazione esplicita
# all'eccezione del target commit-size <600 (effettivo ~645, +45):
# cluster cohesive, 5 funzioni self-contained, zero coupling, rollback
# atomico. Re-export per preservare i path pubblici esistenti:
#   - `from app.core.manipola.transforms.base import <op>`
#   - `from app.core.manipola.transforms import <op>` per le 4 esposte
#     da __init__.py (op_concat_columns, op_rename, op_drop_columns,
#     op_split_column) — `__init__.py` resta `from .base import ...`
#     e cattura il facade. `op_reorder_columns` non e' in init.
#   - `transforms_registry.json` entries "base.<op>" risolte via
#     `getattr(<base module>, "<func>")` dal `PluginExecutor`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .columns import (  # noqa: F401  (re-export)
    op_drop_columns,
    op_rename,
    op_reorder_columns,
    op_concat_columns,
    op_split_column,
)

# Phase 5 B1.5 cutover: cluster FORMULA spostato in
# `app.core.manipola.transforms.formula`. Re-export di entrambi i nomi
# pubblici esistenti — il prefix-underscore `_validate_formula_ast` e'
# **intenzionalmente** re-exported perche' il test contract N4
# (`tests2/security/unit/test_n4_formula_validator_contract.py`) lo
# importa direttamente da `base` (3 occorrenze):
#   - `from app.core.manipola.transforms.base import op_formula`
#   - `from app.core.manipola.transforms.base import _validate_formula_ast`
#   - `from app.core.manipola.transforms import op_formula` (via __init__)
#   - `transforms_registry.json` entry "base.op_formula" risolta via
#     `getattr(<base module>, "op_formula")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .formula import (  # noqa: F401  (re-export)
    op_formula,
    _validate_formula_ast,
)

# Phase 5 B1.6 cutover: cluster CLEANSING (op_fill_na + op_replace +
# op_type_cast) spostato in `app.core.manipola.transforms.cleansing`.
# I 2 helper privati `_is_strict_numeric_series` e
# `_coerce_fill_value_for_numeric` restano single-source nel nuovo
# modulo (verificato pre-extraction: zero consumer esterni). Re-export
# delle 3 funzioni pubbliche per preservare i path:
#   - `from app.core.manipola.transforms.base import op_fill_na`
#       (idem op_replace, op_type_cast)
#   - `from app.core.manipola.transforms import op_fill_na` /
#     `op_replace` / `op_type_cast` — `__init__.py` resta `from .base
#     import ...` e cattura il facade.
#   - `transforms_registry.json` entries "base.op_fill_na" /
#     "base.op_replace" / "base.op_type_cast" risolte via
#     `getattr(<base module>, "<func>")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .cleansing import (  # noqa: F401  (re-export)
    op_fill_na,
    op_replace,
    op_type_cast,
)

# Phase 5 B1.7 cutover: cluster AGGREGATION (op_aggregate, op_sort,
# op_distinct, op_dedup_advanced, op_window_function) spostato in
# `app.core.manipola.transforms.aggregation`. Re-export per preservare
# i path pubblici esistenti:
#   - `from app.core.manipola.transforms.base import <op>` per tutte e 5
#   - `from app.core.manipola.transforms import op_aggregate` /
#     `op_sort` / `op_distinct` (via __init__) — `__init__.py` resta
#     `from .base import ...` e cattura il facade. `op_dedup_advanced`
#     e `op_window_function` non sono in init (verificato pre-extraction).
#   - `transforms_registry.json` entries "base.op_*" risolte via
#     `getattr(<base module>, "<func>")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .aggregation import (  # noqa: F401  (re-export)
    op_aggregate,
    op_sort,
    op_distinct,
    op_dedup_advanced,
    op_window_function,
)

# Phase 5 B1.8 cutover: cluster FILTERING (op_filter, op_conditional)
# spostato in `app.core.manipola.transforms.filtering`. Il private
# helper `_evaluate_condition` resta single-source nel nuovo modulo
# (verificato pre-extraction: zero consumer esterni, usato solo da
# op_conditional). Re-export delle 2 funzioni pubbliche per preservare
# i path:
#   - `from app.core.manipola.transforms.base import op_filter` /
#     `op_conditional`
#   - `from app.core.manipola.transforms import op_filter` /
#     `op_conditional` — `__init__.py` resta `from .base import ...`
#     e cattura il facade.
#   - `transforms_registry.json` entries "base.op_filter" /
#     "base.op_conditional" risolte via `getattr(<base>, "<func>")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .filtering import (  # noqa: F401  (re-export)
    op_filter,
    op_conditional,
)

# Phase 5 B1.9 cutover: cluster ANALYSIS (op_sample, op_lookup, op_bin,
# op_flatten_json, op_statistics) spostato in
# `app.core.manipola.transforms.analysis`. Re-export delle 5 funzioni
# pubbliche per preservare i path:
#   - `from app.core.manipola.transforms.base import <op>`
#   - `from app.core.manipola.transforms import op_statistics` —
#     unica esposta da `__init__.py` (le altre 4 non in init);
#     resta `from .base import op_statistics` e cattura il facade.
#   - `transforms_registry.json` entries "base.op_*" risolte via
#     `getattr(<base module>, "<func>")`.
# Cleanup naturale: `import json` rimosso da base.py (era usato solo
# da op_flatten_json, ora in analysis.py).
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .analysis import (  # noqa: F401  (re-export)
    op_sample,
    op_lookup,
    op_bin,
    op_flatten_json,
    op_statistics,
)

# Phase 5 B1.10 cutover: cluster RESHAPE (op_pivot, op_table_diff,
# op_melt, op_transpose) spostato in
# `app.core.manipola.transforms.reshape`. Re-export per preservare i
# path pubblici esistenti:
#   - `from app.core.manipola.transforms.base import <op>` per tutte e 4
#   - `from app.core.manipola.transforms import op_pivot` /
#     `op_table_diff` (le 2 esposte da __init__.py) — `__init__.py`
#     resta `from .base import ...` e cattura il facade. `op_melt` e
#     `op_transpose` non sono in init.
#   - `transforms_registry.json` entries "base.op_*" risolte via
#     `getattr(<base module>, "<func>")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .reshape import (  # noqa: F401  (re-export)
    op_pivot,
    op_table_diff,
    op_melt,
    op_transpose,
)

# Phase 5 B1.11 cutover: cluster SECURITY (op_md5_hash, op_mask_data
# + 5 helper privati `_mask_*` + dict `MASK_FUNCTIONS`) spostato in
# `app.core.manipola.transforms.security`. Re-export delle 2 funzioni
# pubbliche + del dict `MASK_FUNCTIONS`:
#   - `from app.core.manipola.transforms.base import op_md5_hash` /
#     `op_mask_data`
#   - `from app.core.manipola.transforms import op_md5_hash` /
#     `op_mask_data` — `__init__.py` resta `from .base import ...`
#     e cattura il facade.
#   - `transforms_registry.json` entries "base.op_md5_hash" /
#     "base.op_mask_data" risolte via `getattr(<base>, "<func>")`.
#   - `MASK_FUNCTIONS` re-exported (NON ribindato) per preservare
#     l'identita' del dict object: `base.MASK_FUNCTIONS is
#     security.MASK_FUNCTIONS` → True. Il test
#     `tests2/manipola/unit/test_op_mask_data_fail_closed.py:69,110`
#     usa `patch.dict("...base.MASK_FUNCTIONS", ...)` che muta in-place;
#     la mutazione e' visibile da `op_mask_data` (in security.py) che
#     legge il dict via module-level lookup. NON sostituire con un
#     nuovo dict ne' con MappingProxyType: il contract di patch
#     richiede identita' preservata.
#   - I 5 helper `_mask_*` NON re-exported (verificato: zero consumer
#     esterni). Restano single-source in security.py.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .security import (  # noqa: F401  (re-export)
    op_md5_hash,
    op_mask_data,
    MASK_FUNCTIONS,
)

# Phase 5 B1.11 cutover: op_geo_from_coords (singleton geo) spostato
# in `app.core.manipola.transforms.geo`. Re-export della singola
# funzione pubblica:
#   - `from app.core.manipola.transforms.base import op_geo_from_coords`
#   - `from app.core.manipola.transforms import op_geo_from_coords` —
#     `__init__.py` resta `from .base import op_geo_from_coords` e
#     cattura il facade.
#   - `transforms_registry.json` entry "base.op_geo_from_coords"
#     risolta via `getattr(<base module>, "op_geo_from_coords")`.
# Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1.
from .geo import op_geo_from_coords  # noqa: F401  (re-export)


# Phase 5 B1.7 cutover: op_dedup_advanced + op_window_function moved to
# `transforms.aggregation` (re-exported in testa al file via facade).


# Phase 5 B1.3c cutover: op_uuid_generator moved to `transforms.utility`
# (re-exported in testa al file via facade).
