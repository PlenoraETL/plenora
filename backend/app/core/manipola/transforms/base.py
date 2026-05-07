"""
Operazioni di trasformazione base (non-geospaziali) per MANIPOLA.

Keeps transformation behavior isolated from orchestration callers.
"""

import pandas as pd
import geopandas as gpd
from typing import Union

from app.utils.logger import get_logger

logger = get_logger(__name__)

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# JOIN — Unione tabelle per colonna chiave
# Supporta: inner, left, right, outer, cross. Gestisce suffissi per colonne omonime.

from .joins import op_join, op_union, op_cross_join  # noqa: F401  (re-export, consumato via getattr)

op_concat = op_union


# Phase 5 B1.11 cutover: cluster SECURITY (op_md5_hash + op_mask_data
# + 5 helper privati `_mask_*` + dict `MASK_FUNCTIONS`) spostato in
# `app.core.manipola.transforms.security`. Re-export in coda al file
# via facade. I 5 helper privati restano single-source nel nuovo
# modulo (verificato pre-extraction: zero consumer esterni).
#
# NOTE: — patch.dict identita' oggetto:
# `MASK_FUNCTIONS` e' re-exported da `base.py` via `from .security
# import MASK_FUNCTIONS` (NON ribindato): il test contract
# `tests2/manipola/unit/test_op_mask_data_fail_closed.py:69,110`
# patcha `"app.core.manipola.transforms.base.MASK_FUNCTIONS"` con
# `patch.dict` (mutazione in-place). Poiche' base e security puntano
# allo stesso dict object, la mutazione e' visibile da `op_mask_data`
# (in security.py) che legge il dict via module-level lookup. Vedi
# docstring `transforms/security.py` sezione "Coupling".


from .strings import (  # noqa: F401  (re-export)
    op_string_pad,
    op_string_length,
    op_string_extract,
    op_text_normalize,
)

from .utility import (  # noqa: F401  (re-export)
    op_add_row_number,
    op_date_extract,
    op_uuid_generator,
)

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

from .cleansing import (  # noqa: F401  (re-export)
    op_fill_na,
    op_replace,
    op_type_cast,
)

from .aggregation import (  # noqa: F401  (re-export)
    op_aggregate,
    op_sort,
    op_distinct,
    op_dedup_advanced,
    op_window_function,
)

from .filtering import (  # noqa: F401  (re-export)
    op_filter,
    op_conditional,
)

from .analysis import (  # noqa: F401  (re-export)
    op_sample,
    op_lookup,
    op_bin,
    op_flatten_json,
    op_statistics,
)

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

from .geo import op_geo_from_coords  # noqa: F401  (re-export)
