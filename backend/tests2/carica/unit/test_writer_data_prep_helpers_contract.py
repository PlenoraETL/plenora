from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.core.carica.models import ColumnMapping
from app.core.carica.writer_data_prep import DataPrepMixin
from app.core.carica.writer_data_prep_helpers import (
    build_params,
    fix_mixed_type_columns,
    geometry_to_wkt,
    prepare_dataframe,
)


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)

    def debug(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass


class _Config:
    match_columns = ["id"]


class _Writer(DataPrepMixin):
    config = _Config()


def test_data_prep_helpers_fix_mixed_columns_without_writer_state():
    logger = _Log()
    source = pd.DataFrame({"mixed": [1, "2A", None], "plain": [1, 2, 3]})

    prepared = fix_mixed_type_columns(source.copy(), logger)

    # NaN e' il sentinel canonico per missing nelle Series object di pandas;
    # accettiamo sia None sia NaN (semanticamente equivalenti per i
    # consumer downstream che li trattano come "missing"). NaN != NaN,
    # quindi controlliamo via pandas.isna sul valore in coda.
    mixed_values = prepared["mixed"].tolist()
    assert mixed_values[:2] == ["1", "2A"]
    assert pd.isna(mixed_values[2]) or mixed_values[2] is None
    assert prepared["plain"].tolist() == [1, 2, 3]
    assert logger.messages


def test_prepare_dataframe_does_not_mutate_source_dataframe():
    logger = _Log()
    source = pd.DataFrame({"mixed": [1, "2A"]})

    prepared = prepare_dataframe(source, logger)

    assert prepared is not source
    assert source["mixed"].tolist() == [1, "2A"]
    assert prepared["mixed"].tolist() == ["1", "2A"]


def test_build_params_normalizes_geometry_numpy_datetime_and_match_values():
    logger = _Log()
    row = pd.Series(
        {
            "id": 7,
            "geom": " POINT (1 2) ",
            "when": datetime(2026, 5, 8, tzinfo=timezone.utc),
            "empty": pd.NA,
        }
    )
    columns = [
        ColumnMapping(source_column="geom", dest_column="shape", is_geometry=True),
        ColumnMapping(source_column="when", dest_column="created_at"),
        ColumnMapping(source_column="empty", dest_column="empty_value"),
    ]

    params = build_params(row, columns, "update", ["id"], logger)

    assert params == {
        "shape": "POINT (1 2)",
        "created_at": "2026-05-08T00:00:00+00:00",
        "empty_value": None,
        "match_id": 7,
    }


def test_data_prep_mixin_is_a_thin_compat_delegate():
    writer = _Writer()
    row = pd.Series({"id": 1, "geom": "POINT (0 0)"})
    columns = [ColumnMapping(source_column="geom", dest_column="geom", is_geometry=True)]

    assert writer._geometry_to_wkt(" POINT (0 0) ") == geometry_to_wkt(
        " POINT (0 0) ", _Log()
    )
    assert writer._build_params(row, columns, "update") == {
        "geom": "POINT (0 0)",
        "match_id": 1,
    }
