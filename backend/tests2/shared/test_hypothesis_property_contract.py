"""Property-based contract tests su funzioni pure e sanitizers piccoli."""

from __future__ import annotations

from pathlib import Path
import shutil
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from hypothesis import given
from hypothesis import settings
from hypothesis import strategies as st

import app.core.imdf.router as imdf_router
from app.core.imdf.arcgis.sql_helpers import _quote_sql_value
from app.core.imdf.router import _safe_bundle_dir
from app.core.imdf.validator import _coarse_type_from_dtype, _coarse_type_from_esri


pytestmark = [pytest.mark.unit, pytest.mark.contract]

_COARSE_TYPES = {"string", "int", "float", "date", "geometry", "other"}


class _FakeDType:
    def __init__(self, value: str):
        self._value = value

    def __str__(self) -> str:
        return self._value


@settings(deadline=None)
@given(st.text())
def test_quote_sql_value_strings_roundtrip_quotes_and_escapes(value: str):
    assert _quote_sql_value(value) == "'" + value.replace("'", "''") + "'"


@given(st.integers())
def test_quote_sql_value_integers_are_rendered_without_quotes(value: int):
    assert _quote_sql_value(value) == str(value)


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_quote_sql_value_floats_are_rendered_without_quotes(value: float):
    assert _quote_sql_value(value) == repr(value)


@given(st.booleans())
def test_quote_sql_value_booleans_are_arcgis_compatible(value: bool):
    assert _quote_sql_value(value) == ("1" if value else "0")


@given(st.text())
def test_coarse_type_from_esri_never_crashes_and_returns_known_bucket(value: str):
    assert _coarse_type_from_esri(value) in _COARSE_TYPES


@given(st.text())
def test_coarse_type_from_dtype_string_like_never_crashes_and_returns_known_bucket(
    value: str,
):
    assert _coarse_type_from_dtype(_FakeDType(value)) in _COARSE_TYPES


@settings(deadline=None)
@given(st.text(min_size=0, max_size=40))
def test_safe_bundle_dir_random_input_either_rejects_or_stays_within_root(
    value: str,
):
    original_settings = imdf_router.settings
    root = Path.cwd() / ".pytest-run" / "hypothesis-bundles" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    imdf_router.settings = SimpleNamespace(
        IMDF_BUNDLES_DIR=root,
        IMDF_BUNDLE_TTL_HOURS=24,
    )
    try:
        try:
            resolved = _safe_bundle_dir(value)
        except HTTPException as exc:
            assert exc.status_code == 400
            return

        assert resolved.is_absolute()
        assert resolved.relative_to(root.resolve()) is not None
    finally:
        imdf_router.settings = original_settings
        shutil.rmtree(root.parent, ignore_errors=True)
