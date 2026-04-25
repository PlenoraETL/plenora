"""Helper per il smoke di reconciliation E2E PG (T-08 / D2).

Genera dataset deterministico, esegue DDL/INSERT su PG ext, calcola
checksum simmetrici (count, sum, ordered MD5) per il confronto
source <-> destination.

Scope:
  - dataset deterministico: int, NUMERIC(12,2) come centesimi interi
    espressi come Decimal stabile, text con accenti italiani
  - NO NaN/Inf: P1-04 e' coperto da test unit dedicati, qui evitiamo
    ambiguita' SQL/pandas
  - checksum a 3 metriche complementari per diagnosi del drift
  - cleanup dei nomi tabella e' responsabilita' del caller (vedi
    `make_it_id` in `harness.db`)

Tutto opera su `postgres_ext_conn()` (PG ext locale, credenziali test
hardcoded). Niente DSN dinamici, niente fallback su DB applicativo.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

from tests2.integration.harness.postgres_ext import (
    postgres_ext_conn,
)


# ---------------------------------------------------------------------------
# Dataset deterministico
# ---------------------------------------------------------------------------

# Mix di nomi italiani + ASCII puro per coprire encoding cross-DB.
_NAMES_POOL = (
    "Roma", "Milano", "Napoli", "Torino", "Genova", "Bologna",
    "Cagliari", "Trento", "Aosta", "Perugia", "Bari", "Palermo",
)
# Mix di payload con accenti italiani (UTF-8) + ASCII (regression encoding).
_PAYLOAD_TAGS = (
    "città", "perché", "università", "così", "gioventù",
    "plain", "ASCII-only", "n/a",
)


def gen_deterministic_dataset(n: int, seed: int = 42) -> list[dict]:
    """Genera N righe deterministiche (id, name, amount, payload).

    Determinismo: stesso `n` + stesso `seed` => stessa lista identica.
    Riproducibile bit-exact tra esecuzioni del test e tra macchine
    diverse (assumendo Python >= 3.11 con stesso PRNG `random.Random`).

    Tipi:
      - id:      int sequenziale 1..N (PK)
      - name:    text "row_<6digit>_<city>" (UTF-8 ASCII puro)
      - amount:  Decimal con 2 decimali (centesimi stabili, NO float
                 noise). Range -1_000_000.00 .. +1_000_000.00.
      - payload: text con accenti italiani per regressione encoding

    NO NaN/Inf: il smoke vuole reconciliation aritmetica pulita,
    senza interazioni con il P1-04 sentinel JSONB (test unit dedicati
    `test_nan_inf_jsonb_roundtrip_contract.py`).
    """
    rng = random.Random(seed)
    rows: list[dict] = []
    for i in range(1, n + 1):
        city = _NAMES_POOL[rng.randrange(len(_NAMES_POOL))]
        # amount come centesimi int -> Decimal /100: stabile, niente float drift.
        cents = rng.randint(-100_000_000, 100_000_000)
        amount = Decimal(cents) / Decimal(100)
        payload = _PAYLOAD_TAGS[rng.randrange(len(_PAYLOAD_TAGS))]
        rows.append({
            "id": i,
            "name": f"row_{i:06d}_{city}",
            "amount": amount,
            "payload": payload,
        })
    return rows


# ---------------------------------------------------------------------------
# DDL / INSERT su PG ext
# ---------------------------------------------------------------------------

# Schema esplicito: NUMERIC(12,2) preserva la precisione esatta dei
# Decimal generati. Usare DOUBLE PRECISION qui introdurrebbe drift
# float-side che non e' lo scope del test (vedi P1-04 dedicato).
_CREATE_TABLE_SQL = """
CREATE TABLE public.{table} (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    payload TEXT NOT NULL
)
"""


def create_source_table(table_name: str) -> None:
    """Crea la source table effimera su PG ext (schema `public`)."""
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL.format(table=table_name))


def insert_rows_bulk(table_name: str, rows: list[dict]) -> None:
    """Bulk INSERT via `executemany`. Niente COPY: rispecchia il path
    pandas-side che genera multi-INSERT su `executemany`-class del writer.
    """
    sql = (
        f"INSERT INTO public.{table_name} (id, name, amount, payload) "
        f"VALUES (%(id)s, %(name)s, %(amount)s, %(payload)s)"
    )
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)


def drop_table_if_exists(table_name: str) -> None:
    """Cleanup helper. Usato in `finally` del test, idempotente."""
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS public.{table_name}")


# ---------------------------------------------------------------------------
# Checksum simmetrici source vs destination
# ---------------------------------------------------------------------------

# Ordered MD5: STRING_AGG con separatore esplicito per evitare collisioni
# tra valori adiacenti. ORDER BY id garantisce determinismo dell'aggregazione.
#
# `amount::numeric(12,2)` esplicito su entrambe le tabelle: la source ha
# colonna NUMERIC(12,2) e il cast e' no-op; la destination puo' essere
# scritta dal writer come TEXT (DDL inferito da pandas dtype=object dei
# Decimal client-side), e in quel caso il cast `::numeric(12,2)` riallinea
# il tipo per il SUM e produce un format stabile per il MD5. Senza il
# cast esplicito, SUM su colonna TEXT solleva
# `UndefinedFunction: function sum(text) does not exist`.
_CHECKSUM_SQL_TEMPLATE = """
SELECT
    COUNT(*)::bigint                                                  AS row_count,
    COALESCE(SUM(amount::numeric(12,2)), 0)::numeric(20,2)            AS sum_amount,
    MD5(
        STRING_AGG(
            id::text || '|' || name || '|' || amount::numeric(12,2)::text || '|' || payload,
            chr(10) ORDER BY id
        )
    )                                                                 AS md5_ordered
FROM {schema}.{table}
"""


def compute_checksums(schema: str, table: str) -> dict[str, Any]:
    """Calcola le 3 metriche di reconciliation su una tabella PG ext.

    Returns:
        dict con chiavi:
          - 'row_count': int
          - 'sum_amount': Decimal (NUMERIC stabile, no float drift)
          - 'md5_ordered': str (32 hex chars)
    """
    sql = _CHECKSUM_SQL_TEMPLATE.format(schema=schema, table=table)
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
    # cursor_factory e' RealDictCursor, accesso per nome colonna.
    return {
        "row_count": int(row["row_count"]),
        "sum_amount": row["sum_amount"],
        "md5_ordered": row["md5_ordered"],
    }


def assert_reconciliation_match(src: dict, dest: dict) -> None:
    """Asserisce che le 3 metriche src e dest siano identiche.

    Diagnosi differenziata per facilitare triage:
      - count mismatch  -> probabile batch mancante / duplicato
      - sum mismatch    -> drift type cast int<->float<->decimal
      - md5 mismatch    -> permutazione cross-colonna (catch F-DATA-03)
    """
    if src["row_count"] != dest["row_count"]:
        raise AssertionError(
            f"[reconciliation] count mismatch: "
            f"src={src['row_count']} vs dest={dest['row_count']}. "
            f"Probabile batch mancante o duplicato sulla scrittura."
        )
    if src["sum_amount"] != dest["sum_amount"]:
        raise AssertionError(
            f"[reconciliation] sum mismatch: "
            f"src={src['sum_amount']} vs dest={dest['sum_amount']} "
            f"(delta={dest['sum_amount'] - src['sum_amount']}). "
            f"Probabile drift type cast int<->float<->decimal nel writer."
        )
    if src["md5_ordered"] != dest["md5_ordered"]:
        raise AssertionError(
            f"[reconciliation] md5_ordered mismatch (count+sum identici): "
            f"src={src['md5_ordered']} vs dest={dest['md5_ordered']}. "
            f"Probabile permutazione cross-colonna o reordering "
            f"silenzioso (catch F-DATA-03)."
        )
