"""
tests/features/unit/conftest.py
================================
Setup sys.path per i test unitari del modulo features.
"""

import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")
sys.path.insert(0, str(Path(__file__).parent))  # unit/ directory
