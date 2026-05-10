"""CLI per generare schema/seed/volume SQL deterministici.

Esempi:

    # seed PostgreSQL su stdout
    python3 generator.py --dialect postgres --output seed

    # schema MySQL su file
    python3 generator.py --dialect mysql --output schema --out-dir ./out

    # volume Oracle a 1M righe
    python3 generator.py --dialect oracle --output volume --rows-events 1000000

    # tutti i dialetti, output completo (schema + seed + volume)
    python3 generator.py --dialect all --output all --rows-events 10000 --out-dir ./out

Il default (senza ``--out-dir``) scrive su stdout (un solo dialetto/output).
Con ``--out-dir`` viene rispettata la stessa struttura di ``test-e2e/sql/``:

    out/
      postgres/00_schema.sql
      postgres/01_seed.sql
      postgres/02_volume.sql
      mysql/...
      oracle/...
      sqlserver/...

NB: ``--dialect mssql`` e ``--dialect sqlserver`` sono equivalenti
(alias). Il path ``out/sqlserver/`` viene comunque usato come
directory di output per coerenza con i file esistenti.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

from . import dialect_emit, fixtures, schema_emit
from .models import SeedDataset


# Mapping dialect-name -> filesystem dir name (matches test-e2e/sql/)
_DIR_NAME = {
    "postgres": "postgres",
    "mysql":    "mysql",
    "oracle":   "oracle",
    "mssql":    "sqlserver",
}


def _normalize_dialect(d: str) -> str:
    """Normalizza alias dialetto: ``sqlserver`` -> ``mssql``."""
    if d == "sqlserver":
        return "mssql"
    return d


def emit_seed(ds: SeedDataset, dialect: str) -> str:
    """Render seed completo (no schema, no volume) per un dialetto.

    Ordine FK-safe (rispecchia ``01_seed.sql``):
        regions, cities, customers, products, orders, order_items,
        employees, routes.
    """
    parts: list[str] = []
    header = f"-- Plenora E2E test dataset — seed {dialect} (generato)\n\n"
    parts.append(header)
    parts.append("-- regions\n")
    parts.append(dialect_emit.emit_regions(ds.regions, dialect))
    parts.append("\n-- cities\n")
    parts.append(dialect_emit.emit_cities(ds.cities, dialect))
    parts.append("\n-- customers\n")
    parts.append(dialect_emit.emit_customers(ds.customers, dialect))
    parts.append("\n-- products\n")
    parts.append(dialect_emit.emit_products(ds.products, dialect))
    parts.append("\n-- orders\n")
    parts.append(dialect_emit.emit_orders(ds.orders, dialect))
    parts.append("\n-- order_items\n")
    parts.append(dialect_emit.emit_order_items(ds.order_items, dialect))
    parts.append("\n-- employees\n")
    parts.append(dialect_emit.emit_employees(ds.employees, dialect))
    parts.append("\n-- routes\n")
    parts.append(dialect_emit.emit_routes(ds.routes, dialect))
    if dialect == "oracle":
        parts.append("\nCOMMIT;\n")
    return "".join(parts)


def emit_volume(ds: SeedDataset, dialect: str) -> str:
    """Render solo events_log (volume) per un dialetto."""
    parts = [
        f"-- Plenora E2E test dataset — volume {dialect} (generato)\n",
        f"-- {len(ds.events_log)} righe in events_log (deterministiche).\n\n",
        dialect_emit.emit_events_log(ds.events_log, dialect),
    ]
    if dialect == "oracle":
        parts.append("\nCOMMIT;\n")
    return "".join(parts)


# ── CLI ─────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="seed_generator",
        description="Generatore deterministico di schema/seed/volume SQL "
                    "multi-dialect per i test E2E plenora.",
    )
    p.add_argument(
        "--dialect", required=True,
        choices=["postgres", "mysql", "oracle", "mssql", "sqlserver", "all"],
        help="Dialetto SQL di destinazione (`all` -> tutti).",
    )
    p.add_argument(
        "--output", required=True,
        choices=["schema", "seed", "volume", "all"],
        help="Cosa generare: solo schema (DDL), solo seed (INSERT base), "
             "solo volume (events_log), o tutto.",
    )
    p.add_argument(
        "--rows-events", type=int, default=fixtures.DEFAULT_ROWS_EVENTS,
        help=f"Numero righe events_log per `volume` (default: "
             f"{fixtures.DEFAULT_ROWS_EVENTS}).",
    )
    p.add_argument(
        "--rows-customers-base", type=int, default=12,
        help="Customer base block (default: 12). Usato a fini di documentazione "
             "/ future espansioni; al momento il blocco e' fisso.",
    )
    p.add_argument(
        "--no-edge", action="store_true",
        help="Esclude i blocchi edge cases (id discontinui).",
    )
    p.add_argument(
        "--no-null", action="store_true",
        help="Esclude i blocchi NULL cases.",
    )
    p.add_argument(
        "--no-routes", action="store_true",
        help="Esclude la tabella routes.",
    )
    p.add_argument(
        "--out-dir", type=Path, default=None,
        help="Directory di output. Se omessa scrive su stdout (richiede un "
             "solo (dialect, output) selezionato).",
    )
    return p


def _dialects_for(arg: str) -> Iterable[str]:
    arg = _normalize_dialect(arg)
    if arg == "all":
        return ("postgres", "mysql", "oracle", "mssql")
    return (arg,)


def _outputs_for(arg: str) -> Iterable[str]:
    if arg == "all":
        return ("schema", "seed", "volume")
    return (arg,)


def _write(content: str, *, out_dir: Path | None, dialect: str, kind: str) -> None:
    """Scrive ``content`` su file sotto ``out_dir/<dirname>/<NN_kind>.sql``,
    o su stdout se ``out_dir`` e' None."""
    if out_dir is None:
        sys.stdout.write(content)
        return
    sub = out_dir / _DIR_NAME[dialect]
    sub.mkdir(parents=True, exist_ok=True)
    fname = {
        "schema": "00_schema.sql",
        "seed":   "01_seed.sql",
        "volume": "02_volume.sql",
    }[kind]
    (sub / fname).write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dialects = list(_dialects_for(args.dialect))
    outputs = list(_outputs_for(args.output))

    # stdout requires exactly 1 dialect AND 1 output, else l'output diventa
    # ambiguo. Deleghiamo a --out-dir per il multi-target.
    if args.out_dir is None and (len(dialects) > 1 or len(outputs) > 1):
        sys.stderr.write(
            "Errore: --dialect=all o --output=all richiedono --out-dir.\n"
        )
        return 2

    # Costruiamo il dataset una sola volta (pure function, deterministic).
    rows_events = args.rows_events if "volume" in outputs else 0
    ds = fixtures.build_dataset(
        include_edge=not args.no_edge,
        include_null=not args.no_null,
        include_routes=not args.no_routes,
        rows_events=rows_events,
    )

    for dialect in dialects:
        for kind in outputs:
            if kind == "schema":
                content = schema_emit.emit_schema(dialect)
            elif kind == "seed":
                content = emit_seed(ds, dialect)
            elif kind == "volume":
                content = emit_volume(ds, dialect)
            else:  # pragma: no cover
                raise AssertionError(f"unknown kind: {kind}")
            _write(content, out_dir=args.out_dir, dialect=dialect, kind=kind)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
