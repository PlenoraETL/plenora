"""Lookup schema column names and presence must match the executed transform."""

import pandas as pd
import pytest

from app.core.manipola.transforms.analysis import op_lookup
from app.core.projects.schema_inference import ColumnSchema, SchemaInferenceService


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.mark.parametrize("mapping_config", [{}, {"mapping": None}, {"mapping": {}},
                                           {"mapping": []}, {"mapping": {"a": "mapped"}}])
@pytest.mark.parametrize("unmatched_config", [{}, {"unmatched": None}, {"unmatched": ""},
                                             {"unmatched": "keep"}, {"unmatched": "null"},
                                             {"unmatched": "value"}])
@pytest.mark.parametrize("output_config", [{}, {"output_column": "new"},
                                          {"output_column": "source"}, {"output_column": "other"}])
def test_lookup_prediction_matches_runtime_columns(mapping_config, unmatched_config, output_config):
    frame = pd.DataFrame({"source": ["a", "b"], "other": ["x", "y"]})
    schema = [ColumnSchema(name=name, type="str") for name in frame.columns]
    config = {"column": "source", **mapping_config, **unmatched_config, **output_config}
    handler = SchemaInferenceService(None)._build_transform_handlers({})["lookup"]
    predicted = handler([schema], config, "lookup")
    # Runtime requires mapping as an argument; absent config resolves to None.
    actual = op_lookup(frame, **({"mapping": None} | config))
    assert [col.name for col in predicted] == list(actual.columns)
    if not config.get("mapping") and config.get("unmatched") in (None, "", "keep"):
        assert [col.name for col in predicted] == list(frame.columns)
        assert [col.type for col in predicted] == [col.type for col in schema]
