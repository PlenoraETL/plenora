"""Fingerprint portability without weakening value/schema parity."""
import math
import os

import pandas as pd
import pytest

from tests2.contracts.a3_runtime_corpus import _digest

pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.mark.parametrize("linesep", ["\n", "\r\n"])
def test_fingerprint_is_independent_of_platform_newlines(monkeypatch, linesep):
    monkeypatch.setattr(os, "linesep", linesep)
    assert _digest(pd.DataFrame(columns=["id", "a", "b", "s", "z"])) == "2914c6d5620c"


def test_fingerprint_normalizes_math_library_roundoff_only():
    baseline = pd.DataFrame({"z": [3.6268604078470186, 27.289917197127753]})
    linux = pd.DataFrame({"z": [3.626860407847019, 27.28991719712775]})
    assert _digest(baseline) == _digest(linux)
    assert _digest(baseline) != _digest(baseline + 1e-10)
    assert _digest(baseline.astype(str)) != _digest(linux.astype(str))
    assert _digest(baseline) != _digest(baseline.iloc[::-1])


def test_fingerprint_keeps_text_newlines_and_special_values():
    frame = pd.DataFrame({"s": ["a\nb", "a\r\nb", "1.0000000000000001"],
                          "n": [math.nan, math.inf, -math.inf]})
    changed = frame.copy()
    changed.loc[1, "s"] = "a\nb"
    assert _digest(frame) != _digest(changed)
    changed = frame.copy()
    changed.loc[2, "n"] = math.inf
    assert _digest(frame) != _digest(changed)
