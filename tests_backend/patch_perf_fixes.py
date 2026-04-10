"""
Script da eseguire in tests_v5/ per fixare gli import dei test PERF.
Esegui: python patch_perf_fixes.py
"""
import re
from pathlib import Path

BASE = Path("performance")

INLINE_IMPORTS = '''
import time
from contextlib import contextmanager

# Soglie (secondi) — copiate da conftest per evitare import circolare
THRESHOLDS = {
    "PERF01_preview":       5,
    "PERF02_upload":       60,
    "PERF03_join":        120,
    "PERF04_dissolve":     60,
    "PERF05_pivot":        30,
    "PERF06_enrichment": 1100,
    "PERF07_dedup":        10,
    "PERF08_preview":       5,
    "PERF09_write_100k":   30,
    "PERF10_write_500k":  120,
    "PERF11_feature_svc":  60,
    "PERF14_concurrent":   10,
    "PERF15_scheduler":    90,
}

@contextmanager
def assert_faster_than(seconds: float, label: str = ""):
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    assert elapsed < seconds, (
        f"{label} ha impiegato {elapsed:.2f}s — soglia: {seconds}s"
    )

def assert_df_quality(df, min_rows=1, max_null_pct=0.01, label="DataFrame"):
    assert df is not None
    assert not df.empty
    assert len(df) >= min_rows
    null_pct = df.isnull().sum().sum() / (len(df) * len(df.columns))
    assert null_pct <= max_null_pct

def assert_gdf_quality(gdf, min_rows=1, label="GeoDataFrame"):
    assert_df_quality(gdf, min_rows=min_rows, label=label)
    assert gdf.geometry.notna().all()
    assert (~gdf.geometry.is_valid).sum() == 0
    assert gdf.crs is not None
'''

# Fix all test files
all_files = list(BASE.glob("**/*.py"))
all_files = [f for f in all_files if f.name.startswith("test_")]

for fpath in all_files:
    content = fpath.read_text(encoding="utf-8")
    
    # Remove the problematic import
    content = re.sub(
        r'from performance\.conftest import[^\n]+\n',
        '',
        content
    )
    # Also multiline imports
    content = re.sub(
        r'from performance\.conftest import \([^)]+\)',
        '',
        content,
        flags=re.DOTALL
    )
    
    # Add inline definitions after the last regular import
    # Find position after last import block
    lines = content.split('\n')
    last_import_idx = 0
    for i, line in enumerate(lines):
        if line.startswith('import ') or line.startswith('from '):
            last_import_idx = i
    
    lines.insert(last_import_idx + 1, INLINE_IMPORTS)
    content = '\n'.join(lines)
    
    fpath.write_text(content, encoding="utf-8")
    print(f"✓ {fpath}")

print("\nFatto! Ora aggiungi __init__.py alle cartelle performance/ e performance/unit/ e performance/integration/")
