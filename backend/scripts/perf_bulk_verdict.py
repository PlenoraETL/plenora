#!/usr/bin/env python3
"""Batch 3.12 — calcolo verdetto post-osservazione su bulk MySQL / PG.

Applica meccanicamente le soglie dichiarate in
`orchestrate/phase_2_hardening/kickoff.md §9.2` (MySQL LOAD DATA
decommission criterion) e `§9.3` (Postgres COPY keep-confirmed /
regression-alert criterion) sui log strutturati prodotti da Batch
3.1/3.2/3.4/3.5 e resi query-abili da Batch 3.10 (whitelist extras in
`logs.details` JSONB).

Obiettivo: ridurre a zero l'ambiguita' post-osservazione. Un singolo
comando produce il verdetto univoco per ciascun ramo.

Uso:
    python backend/scripts/perf_bulk_verdict.py --from 2026-05-12 --to 2026-05-26

Esempio di output (numeri sintetici):

    === Verdetto §9.2 MySQL LOAD DATA ===
    Periodo: 2026-05-12 -> 2026-05-26
    N_success_mysql:               42
    N_fallback_disabled (1148):    18
    Sample totale:                 60
    success_rate_mysql:            70.00%
    Verdetto: PERIODO INVALIDO - sample 60 < 100 (§9.2). Estendere.

    === Verdetto §9.3 Postgres COPY ===
    Periodo: 2026-05-12 -> 2026-05-26
    N_success_pg:                  4213
    N_fallback_operational:        3
    Sample totale:                 4216
    success_rate_pg:               99.93%
    Verdetto: KEEP CONFERMATO - rate 99.93% >= 95% (§9.3).

    === Gap §6.R2 — stage_swap MySQL ===
    Invocazioni (event=stage_swap.dispatched dialect=mysql): 0
    -> OK, nessun stage+swap MySQL nel periodo. Rimozione LOAD DATA
       safe lato stage+swap.

Exit code:
    0 - tutte le query OK (verdetto prodotto, anche PERIODO INVALIDO)
    1 - errore DB / query / parsing
    2 - argomenti CLI invalidi (gestito da argparse)

NON tocca runtime applicativo. Legge solo la tabella `logs` esistente.
Soglie copiate verbatim da `§9.2` / `§9.3` — se si modificano li', va
aggiornato anche questo file.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple


# Soglie — copiate VERBATIM da kickoff.md §9.2 / §9.3 (non reinterpretare)

# §9.2 MySQL LOAD DATA (decommission criterion)
MYSQL_MIN_SAMPLE = 100
MYSQL_RATE_PROPOSAL_DEPRECATION = 0.05   # rate < 5%  -> proposta deprecazione
MYSQL_RATE_STRATIFICATION = 0.30         # 5% <= rate < 30% -> stratificazione

# §9.3 PG COPY (keep-confirmed / regression-alert criterion)
PG_MIN_SAMPLE = 1000
PG_RATE_KEEP_CONFIRMED = 0.95            # rate >= 95% -> keep confermato
PG_RATE_OBSERVATION = 0.90               # 90% <= rate < 95% -> osservazione

# §9.2 stratificazione per-tenant: soglia di esclusione tenant micro
STRATIFICATION_MIN_TENANT_SAMPLE = 10


QUERY_MYSQL_RATE = """
    SELECT
      COUNT(*) FILTER (WHERE details->>'event' = 'bulk.success') AS n_success,
      COUNT(*) FILTER (WHERE details->>'event' = 'bulk.fallback'
                        AND details->>'reason' = 'server_disable_local_infile'
                      ) AS n_fallback_disabled
    FROM logs
    WHERE details->>'dialect' = 'mysql'
      AND ts >= %s AND ts < %s
"""

QUERY_PG_RATE = """
    SELECT
      COUNT(*) FILTER (WHERE details->>'event' = 'bulk.success') AS n_success,
      COUNT(*) FILTER (WHERE details->>'event' = 'bulk.fallback'
                        AND details->>'reason' = 'copy_error'
                      ) AS n_fallback_operational
    FROM logs
    WHERE details->>'dialect' = 'postgres'
      AND ts >= %s AND ts < %s
"""

QUERY_STAGE_SWAP_MYSQL = """
    SELECT
      COUNT(*) AS n_dispatched,
      COUNT(DISTINCT details->>'user_id') AS n_distinct_users
    FROM logs
    WHERE details->>'event' = 'stage_swap.dispatched'
      AND details->>'dialect' = 'mysql'
      AND ts >= %s AND ts < %s
"""

# Stratificazione per-tenant MySQL — eseguita SOLO se il rate aggregato
# cade nella fascia 5% <= rate < 30% (§9.2 "zona di ambiguita'")
QUERY_MYSQL_BY_TENANT = """
    SELECT
      COALESCE(details->>'user_id', '<no_user_id>') AS user_id,
      COUNT(*) FILTER (WHERE details->>'event' = 'bulk.success') AS n_success,
      COUNT(*) FILTER (WHERE details->>'event' = 'bulk.fallback'
                        AND details->>'reason' = 'server_disable_local_infile'
                      ) AS n_fallback
    FROM logs
    WHERE details->>'dialect' = 'mysql'
      AND ts >= %s AND ts < %s
    GROUP BY COALESCE(details->>'user_id', '<no_user_id>')
    HAVING COUNT(*) >= %s
    ORDER BY (
      COUNT(*) FILTER (WHERE details->>'event' = 'bulk.success')
      + COUNT(*) FILTER (WHERE details->>'event' = 'bulk.fallback'
                          AND details->>'reason' = 'server_disable_local_infile')
    ) DESC
"""


# Verdetti (funzioni pure, zero dipendenza DB — testate in
# backend/tests2/carica/unit/test_perf_bulk_verdict_thresholds.py)


@dataclass
class Verdict:
    code: str             # es. "KEEP", "PROPOSTA DEPRECAZIONE", ...
    rate: Optional[float] # None se periodo invalido
    rationale: str        # spiegazione breve human-readable


def compute_verdict_mysql(n_success: int, n_fallback_disabled: int) -> Verdict:
    """Applica §9.2. Denominator: success + fallback(server_disable_local_infile).
    Esclusioni by-design: geometry, load_data_error, cursor_acquisition_failure
    (gia' non contate a monte dalla query QUERY_MYSQL_RATE)."""
    sample = n_success + n_fallback_disabled
    if sample < MYSQL_MIN_SAMPLE:
        return Verdict(
            code="PERIODO INVALIDO",
            rate=None,
            rationale=(
                f"sample {sample} < {MYSQL_MIN_SAMPLE} (§9.2). "
                f"Estendere finestra a 4 settimane."
            ),
        )
    rate = n_success / sample
    if rate < MYSQL_RATE_PROPOSAL_DEPRECATION:
        return Verdict(
            code="PROPOSTA DEPRECAZIONE",
            rate=rate,
            rationale=(
                f"rate {rate:.2%} < {MYSQL_RATE_PROPOSAL_DEPRECATION:.0%} (§9.2). "
                f"Candidato a rimozione in Fase 3."
            ),
        )
    if rate < MYSQL_RATE_STRATIFICATION:
        return Verdict(
            code="STRATIFICAZIONE PER-TENANT",
            rate=rate,
            rationale=(
                f"rate {rate:.2%} in zona "
                f"{MYSQL_RATE_PROPOSAL_DEPRECATION:.0%}-"
                f"{MYSQL_RATE_STRATIFICATION:.0%} (§9.2). "
                f"Valutare disable per-tenant via CARICA_BULK_INSERT=off."
            ),
        )
    return Verdict(
        code="KEEP",
        rate=rate,
        rationale=(
            f"rate {rate:.2%} >= {MYSQL_RATE_STRATIFICATION:.0%} (§9.2). "
            f"Path mantenuto."
        ),
    )


def compute_verdict_pg(n_success: int, n_fallback_operational: int) -> Verdict:
    """Applica §9.3. Denominator: success + fallback(copy_error).
    Esclusioni by-design: geometry, driver_unsupported, cursor_acquisition_failure.
    Soglie invertite rispetto a §9.2 — PG rate alto = sano."""
    sample = n_success + n_fallback_operational
    if sample < PG_MIN_SAMPLE:
        return Verdict(
            code="PERIODO INVALIDO",
            rate=None,
            rationale=(
                f"sample {sample} < {PG_MIN_SAMPLE} (§9.3). "
                f"Estendere finestra a 4 settimane o attendere traffico."
            ),
        )
    rate = n_success / sample
    if rate >= PG_RATE_KEEP_CONFIRMED:
        return Verdict(
            code="KEEP CONFERMATO",
            rate=rate,
            rationale=(
                f"rate {rate:.2%} >= {PG_RATE_KEEP_CONFIRMED:.0%} (§9.3). "
                f"Nessuna azione."
            ),
        )
    if rate >= PG_RATE_OBSERVATION:
        return Verdict(
            code="OSSERVAZIONE ESTESA",
            rate=rate,
            rationale=(
                f"rate {rate:.2%} in zona "
                f"{PG_RATE_OBSERVATION:.0%}-"
                f"{PG_RATE_KEEP_CONFIRMED:.0%} (§9.3). "
                f"Estendere +2 settimane, investigare cause puntuali."
            ),
        )
    return Verdict(
        code="ALERT REGRESSIONE",
        rate=rate,
        rationale=(
            f"rate {rate:.2%} < {PG_RATE_OBSERVATION:.0%} (§9.3). "
            f"BUG signal — RCA su copy_error, baseline compare, "
            f"mitigazione temporanea CARICA_BULK_INSERT=off."
        ),
    )


def mysql_needs_stratification(verdict: Verdict) -> bool:
    """Trigger stratificazione per-tenant: SOLO se verdetto MySQL cade
    in zona 5-30% (STRATIFICAZIONE PER-TENANT)."""
    return verdict.code == "STRATIFICAZIONE PER-TENANT"


# Output format


def _format_header(title: str) -> str:
    return f"\n=== {title} ==="


def format_mysql_section(
    from_ts: str,
    to_ts: str,
    n_success: int,
    n_fallback_disabled: int,
    verdict: Verdict,
) -> str:
    sample = n_success + n_fallback_disabled
    rate_str = f"{verdict.rate:.2%}" if verdict.rate is not None else "n/d"
    return (
        f"{_format_header('Verdetto §9.2 MySQL LOAD DATA')}\n"
        f"Periodo: {from_ts} -> {to_ts}\n"
        f"N_success_mysql:               {n_success}\n"
        f"N_fallback_disabled (1148):    {n_fallback_disabled}\n"
        f"Sample totale:                 {sample}\n"
        f"success_rate_mysql:            {rate_str}\n"
        f"Verdetto: {verdict.code} - {verdict.rationale}\n"
    )


def format_pg_section(
    from_ts: str,
    to_ts: str,
    n_success: int,
    n_fallback_operational: int,
    verdict: Verdict,
) -> str:
    sample = n_success + n_fallback_operational
    rate_str = f"{verdict.rate:.2%}" if verdict.rate is not None else "n/d"
    return (
        f"{_format_header('Verdetto §9.3 Postgres COPY')}\n"
        f"Periodo: {from_ts} -> {to_ts}\n"
        f"N_success_pg:                  {n_success}\n"
        f"N_fallback_operational:        {n_fallback_operational}\n"
        f"Sample totale:                 {sample}\n"
        f"success_rate_pg:               {rate_str}\n"
        f"Verdetto: {verdict.code} - {verdict.rationale}\n"
    )


def format_stage_swap_section(
    n_dispatched: int, n_distinct_users: int
) -> str:
    if n_dispatched == 0:
        note = (
            "OK, nessun stage+swap MySQL nel periodo. "
            "Rimozione LOAD DATA safe lato stage+swap."
        )
    else:
        note = (
            f"ATTENZIONE: stage+swap MySQL invocato {n_dispatched} volte "
            f"su {n_distinct_users} user distinti. "
            f"Verificare impatto prima di rimuovere LOAD DATA "
            f"(fallback executemany su shadow puo' degradare TRUNCATE_INSERT > 500k)."
        )
    return (
        f"{_format_header('Gap §6.R2 — stage_swap MySQL')}\n"
        f"Invocazioni (event=stage_swap.dispatched dialect=mysql): {n_dispatched}\n"
        f"-> {note}\n"
    )


def format_stratification_section(rows: list) -> str:
    if not rows:
        return (
            f"{_format_header('Stratificazione per-tenant MySQL (§9.2 zona 5-30%)')}\n"
            f"Nessun tenant con sample >= {STRATIFICATION_MIN_TENANT_SAMPLE} nel periodo.\n"
        )
    lines = [
        f"{_format_header('Stratificazione per-tenant MySQL (§9.2 zona 5-30%)')}",
        f"{'user_id':<40} {'n_success':>10} {'n_fallback':>12} {'rate':>8}",
        "-" * 74,
    ]
    for r in rows:
        uid = str(r.get("user_id", "<no_user_id>"))[:40]
        ns = int(r.get("n_success", 0) or 0)
        nf = int(r.get("n_fallback", 0) or 0)
        total = ns + nf
        rate_str = f"{ns / total:.2%}" if total > 0 else "n/d"
        lines.append(f"{uid:<40} {ns:>10} {nf:>12} {rate_str:>8}")
    lines.append(
        "\nTenant con rate molto basso sono candidati a disable per-tenant "
        "via CARICA_BULK_INSERT=off (review §6.3 kill-switch preservato)."
    )
    return "\n".join(lines) + "\n"


# Run (I/O: DB + stdout) — separato da logica pura per testabilita'


def run(from_ts: str, to_ts: str) -> int:
    """Esegue le 3 query canoniche + eventuale stratificazione, stampa
    i verdetti, ritorna exit code."""
    # Import lazy: lo script puo' essere importato per i soli test soglie
    # senza richiedere configurazione DB.
    try:
        from app.db.pg_connection import fetchone, fetchall
    except Exception as e:
        print(f"ERRORE: impossibile importare app.db.pg_connection: {e}", file=sys.stderr)
        return 1

    try:
        # Query 1 — MySQL rate
        row = fetchone(QUERY_MYSQL_RATE, (from_ts, to_ts)) or {}
        mysql_success = int(row.get("n_success", 0) or 0)
        mysql_fallback = int(row.get("n_fallback_disabled", 0) or 0)
        mysql_verdict = compute_verdict_mysql(mysql_success, mysql_fallback)

        # Query 2 — PG rate
        row = fetchone(QUERY_PG_RATE, (from_ts, to_ts)) or {}
        pg_success = int(row.get("n_success", 0) or 0)
        pg_fallback = int(row.get("n_fallback_operational", 0) or 0)
        pg_verdict = compute_verdict_pg(pg_success, pg_fallback)

        # Query 3 — stage_swap MySQL
        row = fetchone(QUERY_STAGE_SWAP_MYSQL, (from_ts, to_ts)) or {}
        n_dispatched = int(row.get("n_dispatched", 0) or 0)
        n_distinct_users = int(row.get("n_distinct_users", 0) or 0)

        # Query 4 (opzionale) — stratificazione per-tenant MySQL
        stratification_rows = []
        if mysql_needs_stratification(mysql_verdict):
            stratification_rows = fetchall(
                QUERY_MYSQL_BY_TENANT,
                (from_ts, to_ts, STRATIFICATION_MIN_TENANT_SAMPLE),
            )
    except Exception as e:
        print(f"ERRORE query DB: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # Output
    print(format_mysql_section(
        from_ts, to_ts, mysql_success, mysql_fallback, mysql_verdict
    ))
    print(format_pg_section(
        from_ts, to_ts, pg_success, pg_fallback, pg_verdict
    ))
    print(format_stage_swap_section(n_dispatched, n_distinct_users))
    if mysql_needs_stratification(mysql_verdict):
        print(format_stratification_section(stratification_rows))

    return 0


# CLI


def _parse_iso_date(s: str) -> str:
    """Valida che `s` sia una data ISO (YYYY-MM-DD o YYYY-MM-DDTHH:MM:SS).
    Ritorna la stringa originale (per passaggio diretto al DB come ts
    comparabile alla colonna ts in `logs`, che e' stringa ISO)."""
    try:
        # Accetta entrambi i formati
        datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"data ISO 8601 attesa (YYYY-MM-DD o YYYY-MM-DDTHH:MM:SS), ricevuto: {s!r}"
        ) from e
    return s


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Calcola verdetto post-osservazione bulk (§9.2 MySQL / §9.3 PG / "
            "stage_swap MySQL) applicando le soglie di kickoff.md."
        ),
    )
    parser.add_argument(
        "--from", dest="from_ts", required=True, type=_parse_iso_date,
        help="Inizio finestra di osservazione (ISO 8601, incluso).",
    )
    parser.add_argument(
        "--to", dest="to_ts", required=True, type=_parse_iso_date,
        help="Fine finestra di osservazione (ISO 8601, escluso).",
    )
    args = parser.parse_args(argv)
    return run(args.from_ts, args.to_ts)


if __name__ == "__main__":
    sys.exit(main())
