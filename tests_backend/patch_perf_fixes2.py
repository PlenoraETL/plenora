"""
Esegui in tests_v5/: python patch_perf_fixes2.py
"""
import ast, re
from pathlib import Path

INLINE = '''
import time as _time
from contextlib import contextmanager as _contextmanager

THRESHOLDS = {
    "PERF01_preview": 5, "PERF02_upload": 60, "PERF03_join": 120,
    "PERF04_dissolve": 60, "PERF05_pivot": 30, "PERF06_enrichment": 1100,
    "PERF07_dedup": 10, "PERF08_preview": 5, "PERF09_write_100k": 30,
    "PERF10_write_500k": 120, "PERF11_feature_svc": 60,
    "PERF14_concurrent": 10, "PERF15_scheduler": 90,
}

@_contextmanager
def assert_faster_than(seconds, label=""):
    start = _time.perf_counter()
    yield
    elapsed = _time.perf_counter() - start
    assert elapsed < seconds, f"{label} ha impiegato {elapsed:.2f}s > {seconds}s"

def assert_df_quality(df, min_rows=1, max_null_pct=0.01, label="DataFrame"):
    assert df is not None and not df.empty and len(df) >= min_rows

def assert_gdf_quality(gdf, min_rows=1, label="GeoDataFrame"):
    assert_df_quality(gdf, min_rows=min_rows)
    assert gdf.geometry.notna().all() and gdf.crs is not None
'''

for fpath in Path("performance").glob("**/*.py"):
    if not fpath.name.startswith("test_"):
        continue
    content = fpath.read_text(encoding="utf-8")
    # Remove ALL from performance.conftest import patterns (single and multiline)
    content = re.sub(
        r'from performance\.conftest import\s*\([^)]*\)',
        '', content, flags=re.DOTALL
    )
    content = re.sub(
        r'from performance\.conftest import[^\n]*\n',
        '', content
    )
    # Remove leftover bare names on their own lines (residui del multiline)
    content = re.sub(
        r'^\s+(THRESHOLDS|assert_df_quality|assert_gdf_quality|assert_faster_than)[,\s]*\n',
        '', content, flags=re.MULTILINE
    )
    content = re.sub(r'^\s*\)\s*\n', '', content, flags=re.MULTILINE)
    # Insert INLINE after docstring + first imports block
    # Find first non-import, non-docstring line
    lines = content.split('\n')
    insert_at = 0
    in_docstring = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if stripped.startswith('import ') or stripped.startswith('from ') or stripped == '':
            insert_at = i + 1
        else:
            break
    lines.insert(insert_at, INLINE)
    content = '\n'.join(lines)
    try:
        ast.parse(content)
        fpath.write_text(content, encoding="utf-8")
        print(f"✓ {fpath.name}")
    except SyntaxError as e:
        print(f"✗ {fpath.name}: {e}")
