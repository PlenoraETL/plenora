"""Bench END-TO-END bulk vs executemany contro PG reale — placeholder.

**Stato**: skip-always. Non esegue confronti fino a che non e' stata
approvata una delle due dep in STREAM_REPORT.md § Deps:
  (a) `testcontainers[postgres]` per container ephemeral
  (b) `PERF_PG_TEST_URL` env var che punti a un PG test

La distinzione vs `test_bench_bulk_vs_executemany_clientside.py`:
  - client-side: misura solo il costo di CSV-gen vs iterrows/dict
    **in processo python**, senza protocollo PG. Risulta in speedup
    ~19x misurato (baseline Batch 0).
  - end-to-end: include network round-trip, WAL fsync, commit. Lo
    speedup atteso su PG reale e' 50-100x per dataset >=100k (la
    letteratura `psycopg2 copy_expert` documenta 10-100x vs
    executemany).

**Per il done-criterion PERF 7.0 la misura client-side e' sufficiente
come prova documentata** (19x >> 3x target). L'end-to-end resta un
nice-to-have che potrebbe essere sbloccato in un follow-up con
approvazione deps.

Esecuzione (una volta attivato):
    PERF_PG_TEST_URL=postgresql+psycopg2://user:pwd@host/db \
        pytest backend/tests2/performance/test_bench_bulk_vs_executemany_pg_endtoend.py \
        -m perf_bench -s -q
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.perf_bench
def test_bench_pg_endtoend_pending_dep_decision():
    """Placeholder documentato: skip finche' deps/env non sono configurati.

    Quando attivato, dovra':
      1. connettersi a `PERF_PG_TEST_URL`
      2. creare una tabella temporanea (es. `pg_temp.perf_bench_<pid>`)
      3. eseguire INSERT 100k rows via bulk_copy_postgres + misurare
      4. eseguire INSERT 100k rows via executemany (forzando CARICA_BULK_INSERT=off)
         + misurare
      5. asserire speedup >= 10x (threshold conservativo; atteso 50-100x)
      6. cleanup

    Fuori scope al momento — vedi STREAM_REPORT.md.
    """
    url = os.getenv("PERF_PG_TEST_URL")
    if not url:
        pytest.skip(
            "PERF_PG_TEST_URL non configurato. "
            "End-to-end bulk vs executemany richiede un Postgres test "
            "(locale o container). "
            "Decisione deps deferrata — vedi STREAM_REPORT.md § Deps."
        )

    # Quando la dep o la env saranno approvate, sostituire il blocco
    # sottostante con l'implementazione secondo docstring. Placeholder
    # esplicito vs silenzioso: se PERF_PG_TEST_URL e' settata ma il
    # test non e' implementato, vogliamo che l'utente lo sappia.
    pytest.skip(
        "Endpoint PG test rilevato ma implementazione end-to-end non "
        "ancora realizzata. Follow-up oltre Fase 1 — vedi Proposte Fase 2 "
        "in STREAM_REPORT.md."
    )
