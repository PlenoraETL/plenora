"""
tests/{modulo}/unit/conftest.py — TEMPLATE
==========================================
Copiare questo file in:
  tests/manipola/unit/conftest.py
  tests/arricchisci/unit/conftest.py
  tests/carica/unit/conftest.py

e adattare gli import al modulo specifico.

Il setup sys.path è identico per tutti i moduli.
"""

import sys
from pathlib import Path

# Cerca la directory `backend/` che contiene `app/__init__.py` risalendo dai parent.
_HERE = Path(__file__).resolve()
_BACKEND = None
for _p in [_HERE, *_HERE.parents]:
    candidate = _p / "backend"
    if (candidate / "app" / "__init__.py").exists():
        _BACKEND = candidate
        break

# Fallback: percorso assoluto noto
if _BACKEND is None:
    _abs = Path(r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")
    if (_abs / "app" / "__init__.py").exists():
        _BACKEND = _abs

if _BACKEND and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
