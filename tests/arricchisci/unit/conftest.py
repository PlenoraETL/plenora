import sys
from pathlib import Path
import pytest

sys.path.insert(0, r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def mock_reset():
    """Override del conftest globale: i test unit non richiedono il mock server."""
    pass
