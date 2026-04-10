"""
tests/logs/unit/conftest.py
===========================
Setup sys.path per i test unitari del modulo logs.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_BACKEND = None
for _p in [_HERE, *_HERE.parents]:
    if (_p / "app" / "__init__.py").exists():
        _BACKEND = _p
        break

if _BACKEND is None:
    for _candidate in [
        Path("C:/Users/Marco/Desktop/int_efm/URBY-ETL/backend"),
        Path(__file__).parents[4] / "URBY-ETL" / "backend",
        Path(__file__).parents[4] / "backend",
    ]:
        if (_candidate / "app" / "__init__.py").exists():
            _BACKEND = _candidate
            break

if _BACKEND and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
