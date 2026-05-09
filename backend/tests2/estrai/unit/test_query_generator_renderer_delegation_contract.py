from __future__ import annotations

import pytest

from app.core.estrai.query_joins import QueryJoinRenderer
from app.core.estrai.query_projection import QueryProjectionRenderer


pytestmark = [
    pytest.mark.unit,
    pytest.mark.estrai,
    pytest.mark.contract,
    pytest.mark.regression,
]


def test_query_generator_renderers_do_not_use_getattr_delegator():
    assert "__getattr__" not in QueryJoinRenderer.__dict__
    assert "__getattr__" not in QueryProjectionRenderer.__dict__
