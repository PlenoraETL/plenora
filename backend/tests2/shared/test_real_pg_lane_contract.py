"""I test su PostgreSQL vero sono TUTTI raggiungibili dal lanciatore.

UI conferma finale (06/11/2026, nota «test su PG vero»): il lanciatore
``backend/scripts/run_real_pg_tests.py`` esegue ``pytest -m real_pg``. Un test
che usa il PostgreSQL vero (``tests2/shared/_real_pg``: DSN, schema
temporaneo, connessione) senza il marcatore ``real_pg`` non girerebbe mai,
nemmeno a mano: qui si vieta. Il limite in CI (nessun PostgreSQL: i test si
saltano) e' dichiarato in ``tests2/shared/_real_pg.py``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

TESTS2 = Path(__file__).resolve().parents[1]
REAL_PG_USES = {"real_pg_schema", "REAL_PG_DSN", "_real_pool", "connect"}


def _decorators(fn: ast.FunctionDef) -> set[str]:
    names = set()
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        names.add(ast.unparse(target))
    return names


def _uses_real_pg(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in REAL_PG_USES - {"connect"}:
            return True
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "_real_pg" and node.attr in REAL_PG_USES:
            return True
    return False


def _real_pg_tests():
    out = []
    for path in sorted(TESTS2.rglob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        if "_real_pg" not in text and "PLENORA_REAL_PG_DSN" not in text:
            continue
        tree = ast.parse(text)
        for fn in tree.body:
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_") and _uses_real_pg(fn):
                out.append((path.relative_to(TESTS2).as_posix(), fn.name, _decorators(fn)))
    return out


def test_every_real_postgres_test_carries_the_real_pg_mark():
    found = _real_pg_tests()
    # Semplificazione H5/H6: tolti i 4 test del lock e del CONCURRENTLY all'avvio, aggiunti 2.
    assert len(found) >= 5, found
    unmarked = [(f, n) for f, n, decs in found if not decs & {"real_pg_marks", "pytest.mark.real_pg"}]
    assert unmarked == []


def test_only_the_shared_module_reads_the_dsn():
    reads = re.compile(r"""environ(\.get\(|\[)\s*["']PLENORA_REAL_PG_DSN""")
    readers = [p.relative_to(TESTS2).as_posix() for p in TESTS2.rglob("*.py")
               if reads.search(p.read_text(encoding="utf-8"))]
    assert readers == ["shared/_real_pg.py"]


def test_launcher_runs_the_marked_tests_and_removes_its_container():
    script = (TESTS2.parent / "scripts" / "run_real_pg_tests.py").read_text(encoding="utf-8")
    assert '"-m", "real_pg"' in script
    assert "postgres:17-alpine" in script
    assert '"rm", "-f", container' in script
    ini = (TESTS2 / "pytest.ini").read_text(encoding="utf-8")
    assert "real_pg:" in ini
