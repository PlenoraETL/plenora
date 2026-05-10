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


from .joins import op_join, op_union, op_cross_join  # noqa: F401 (compatibility export, consumato via getattr

op_concat = op_union


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

# Compatibility export: registry entries and security contract tests import
# formula helpers from this module.
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

# Compatibility export: `MASK_FUNCTIONS` must remain the same dict object as
# `security.MASK_FUNCTIONS` because tests patch it in-place through this module.
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
