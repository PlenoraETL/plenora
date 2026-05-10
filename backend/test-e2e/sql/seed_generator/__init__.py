"""Generator deterministico per il seed E2E plenora.

Modules:
    * :mod:`models`        — dataclass dei record (Region/City/...)
    * :mod:`fixtures`      — dataset baseline (BASE/EDGE/NULL blocks)
    * :mod:`dialect_emit`  — render INSERT idiomatici per dialetto
    * :mod:`schema_emit`   — render schema DDL per dialetto
    * :mod:`generator`     — CLI

Esempio programmatico:

    >>> from seed_generator import fixtures, generator
    >>> ds = fixtures.build_dataset(rows_events=0)
    >>> sql = generator.emit_seed(ds, "postgres")
    >>> "INSERT INTO regions" in sql
    True
"""
# NB: NON importiamo `generator` qui per evitare un RuntimeWarning con
# `python3 -m seed_generator.generator` (sys.modules collision).
from . import dialect_emit, fixtures, models, schema_emit

__all__ = ["dialect_emit", "fixtures", "models", "schema_emit"]
