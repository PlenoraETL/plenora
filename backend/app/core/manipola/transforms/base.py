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
# Supporta: inner, left, right, outer, cross. Gestisce suffissi per colonne omonime

from .joins import op_join, op_union, op_cross_join  # noqa: F401 (compatibility export, consumato via getattr

op_concat = op_union


# B1.11 boundary: cluster SECURITY (op_md5_hash + op_mask_data
# + 5 helper privati `_mask_*` + dict `MASK_FUNCTIONS`) spostato in
# `app.core.manipola.transforms.security`. compatibility export in coda al file
# via compatibility module. I 5 helper privati restano single-source nel nuovo
# NOTE: — patch.dict identita' oggetto
# `MASK_FUNCTIONS` e' compatibility export da `base.py` via `from .security
# import MASK_FUNCTIONS` (NON ribindato): il test contract
# `tests2/manipola/unit/test_op_mask_data_fail_closed.py:69,110`
# patcha `"app.core.manipola.transforms.base.MASK_FUNCTIONS"` con
# `patch.dict` (mutazione in-place). Poiche' base e security puntano
# allo stesso dict object, la mutazione e' visibile da `op_mask_data`
# in security.py) che legge il dict via module-level lookup. Vedi
# docstring `transforms/security.py` sezione "Coupling"


from .strings import (  # noqa: F401 (compatibility export
    op_string_pad,
    op_string_length,
    op_string_extract,
    op_text_normalize,
)

from .utility import (  # noqa: F401 (compatibility export
    op_add_row_number,
    op_date_extract,
    op_uuid_generator,
)

from .columns import (  # noqa: F401 (compatibility export
    op_drop_columns,
    op_rename,
    op_reorder_columns,
    op_concat_columns,
    op_split_column,
)

# B1.5 boundary: cluster FORMULA spostato in
# `app.core.manipola.transforms.formula`. compatibility export di entrambi i nomi
# pubblici esistenti — il prefix-underscore `_validate_formula_ast` e'
# **intenzionalmente** compatibility export perche' il test contract N4
# `tests2/security/unit/test_n4_formula_validator_contract.py`) lo
# importa direttamente da `base` (3 occorrenze
# `from app.core.manipola.transforms.base import op_formula`
# `from app.core.manipola.transforms.base import _validate_formula_ast`
# `from app.core.manipola.transforms import op_formula` (via __init__
# `transforms_registry.json` entry "base.op_formula" risolta via
# `getattr(<base module>, "op_formula")`
from .formula import (  # noqa: F401 (compatibility export
    op_formula,
    _validate_formula_ast,
)

from .cleansing import (  # noqa: F401 (compatibility export
    op_fill_na,
    op_replace,
    op_type_cast,
)

from .aggregation import (  # noqa: F401 (compatibility export
    op_aggregate,
    op_sort,
    op_distinct,
    op_dedup_advanced,
    op_window_function,
)

from .filtering import (  # noqa: F401 (compatibility export
    op_filter,
    op_conditional,
)

from .analysis import (  # noqa: F401 (compatibility export
    op_sample,
    op_lookup,
    op_bin,
    op_flatten_json,
    op_statistics,
)

from .reshape import (  # noqa: F401 (compatibility export
    op_pivot,
    op_table_diff,
    op_melt,
    op_transpose,
)

# B1.11 boundary: cluster SECURITY (op_md5_hash, op_mask_data
# + 5 helper privati `_mask_*` + dict `MASK_FUNCTIONS`) spostato in
# `app.core.manipola.transforms.security`. compatibility export delle 2 funzioni
# pubbliche + del dict `MASK_FUNCTIONS`
# `from app.core.manipola.transforms.base import op_md5_hash` /
# `op_mask_data`
# `from app.core.manipola.transforms import op_md5_hash` /
# `op_mask_data` — `__init__.py` resta `from .base import ...`
# e cattura il compatibility module
# `transforms_registry.json` entries "base.op_md5_hash" /
# "base.op_mask_data" risolte via `getattr(<base>, "<func>")`
# `MASK_FUNCTIONS` compatibility export (NON ribindato) per preservare
# l'identita' del dict object: `base.MASK_FUNCTIONS is
# security.MASK_FUNCTIONS` → True. Il test
# `tests2/manipola/unit/test_op_mask_data_fail_closed.py:69,110`
# usa `patch.dict("...base.MASK_FUNCTIONS", ...)` che muta in-place
# la mutazione e' visibile da `op_mask_data` (in security.py) che
# legge il dict via module-level lookup. NON sostituire con un
# nuovo dict ne' con MappingProxyType: il contract di patch
# richiede identita' preservata
# I 5 helper `_mask_*` NON compatibility export (verificato: zero consumer
# esterni). Restano single-source in security.py
from .security import (  # noqa: F401 (compatibility export
    op_md5_hash,
    op_mask_data,
    MASK_FUNCTIONS,
)

from .geo import op_geo_from_coords  # noqa: F401 (compatibility export

__all__ = [
    "DataFrame",
    "MASK_FUNCTIONS",
    "_validate_formula_ast",
    "op_add_row_number",
    "op_aggregate",
    "op_bin",
    "op_concat",
    "op_concat_columns",
    "op_conditional",
    "op_cross_join",
    "op_date_extract",
    "op_dedup_advanced",
    "op_distinct",
    "op_drop_columns",
    "op_fill_na",
    "op_filter",
    "op_flatten_json",
    "op_formula",
    "op_geo_from_coords",
    "op_join",
    "op_lookup",
    "op_mask_data",
    "op_md5_hash",
    "op_melt",
    "op_pivot",
    "op_rename",
    "op_reorder_columns",
    "op_replace",
    "op_sample",
    "op_sort",
    "op_split_column",
    "op_statistics",
    "op_string_extract",
    "op_string_length",
    "op_string_pad",
    "op_table_diff",
    "op_text_normalize",
    "op_transpose",
    "op_type_cast",
    "op_union",
    "op_uuid_generator",
    "op_window_function",
]
