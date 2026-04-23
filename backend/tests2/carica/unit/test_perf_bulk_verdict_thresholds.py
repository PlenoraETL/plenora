"""Test soglie Batch 3.12 — `backend/scripts/perf_bulk_verdict.py`.

Copre la logica pura di applicazione delle soglie §9.2 (MySQL LOAD DATA
decommission) e §9.3 (PG COPY keep-confirmed / regression-alert) senza
dipendenza DB. Le soglie sono copiate verbatim da kickoff.md; questi
test congelano la loro semantica per evitare drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


# Import dello script: aggiungi `backend/scripts/` al sys.path perche'
# non e' un package installabile, e' un file standalone.
_SCRIPTS_DIR = Path(__file__).parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import perf_bulk_verdict as pbv  # noqa: E402


pytestmark = [pytest.mark.unit, pytest.mark.carica]


# =============================================================================
# §9.2 MySQL — soglie decommission
# =============================================================================


def test_mysql_verdict_sample_insufficiente_returns_invalid():
    """sample < 100 (§9.2) → PERIODO INVALIDO, rate None."""
    v = pbv.compute_verdict_mysql(n_success=40, n_fallback_disabled=20)
    assert v.code == "PERIODO INVALIDO"
    assert v.rate is None
    assert "60" in v.rationale and "100" in v.rationale


def test_mysql_verdict_sample_exactly_at_boundary_100_is_valid():
    """sample == 100 deve essere VALIDO (soglia e' strict <100)."""
    # 5 success + 95 fallback = 100, rate 5% → boundary fra
    # PROPOSTA DEPRECAZIONE (<5%) e STRATIFICAZIONE (5-30%).
    # Secondo §9.2: "< 5%" (strict) — 5.00% cade in STRATIFICAZIONE.
    v = pbv.compute_verdict_mysql(n_success=5, n_fallback_disabled=95)
    assert v.code != "PERIODO INVALIDO"
    assert v.rate == pytest.approx(0.05)


def test_mysql_verdict_below_5pct_is_proposta_deprecazione():
    """rate < 5% → PROPOSTA DEPRECAZIONE."""
    # 2% rate con sample 500
    v = pbv.compute_verdict_mysql(n_success=10, n_fallback_disabled=490)
    assert v.code == "PROPOSTA DEPRECAZIONE"
    assert v.rate == pytest.approx(0.02)
    assert "5%" in v.rationale or "0.05" in v.rationale


def test_mysql_verdict_5pct_boundary_is_stratification():
    """rate == 5% → STRATIFICAZIONE PER-TENANT (strict < 5% per proposta)."""
    v = pbv.compute_verdict_mysql(n_success=25, n_fallback_disabled=475)
    assert v.code == "STRATIFICAZIONE PER-TENANT"
    assert v.rate == pytest.approx(0.05)


def test_mysql_verdict_in_5_to_30_is_stratification():
    """5% ≤ rate < 30% → STRATIFICAZIONE PER-TENANT."""
    v = pbv.compute_verdict_mysql(n_success=75, n_fallback_disabled=425)
    assert v.code == "STRATIFICAZIONE PER-TENANT"
    assert v.rate == pytest.approx(0.15)


def test_mysql_verdict_30pct_boundary_is_keep():
    """rate == 30% → KEEP (strict < 30% per stratificazione)."""
    v = pbv.compute_verdict_mysql(n_success=150, n_fallback_disabled=350)
    assert v.code == "KEEP"
    assert v.rate == pytest.approx(0.30)


def test_mysql_verdict_above_30pct_is_keep():
    """rate >= 30% → KEEP."""
    v = pbv.compute_verdict_mysql(n_success=250, n_fallback_disabled=250)
    assert v.code == "KEEP"
    assert v.rate == pytest.approx(0.50)


def test_mysql_verdict_zero_fallback_perfect_keep():
    """Zero fallback: rate 100% → KEEP."""
    v = pbv.compute_verdict_mysql(n_success=500, n_fallback_disabled=0)
    assert v.code == "KEEP"
    assert v.rate == pytest.approx(1.0)


def test_mysql_verdict_zero_success_full_fallback_proposta_deprec():
    """Zero success: rate 0% → PROPOSTA DEPRECAZIONE (sample sufficiente)."""
    v = pbv.compute_verdict_mysql(n_success=0, n_fallback_disabled=500)
    assert v.code == "PROPOSTA DEPRECAZIONE"
    assert v.rate == pytest.approx(0.0)


# =============================================================================
# §9.3 PG — soglie keep-confirmed / regression-alert
# =============================================================================


def test_pg_verdict_sample_insufficiente_returns_invalid():
    """sample < 1000 (§9.3) → PERIODO INVALIDO."""
    v = pbv.compute_verdict_pg(n_success=450, n_fallback_operational=50)
    assert v.code == "PERIODO INVALIDO"
    assert v.rate is None
    assert "500" in v.rationale and "1000" in v.rationale


def test_pg_verdict_sample_exactly_1000_is_valid():
    """sample == 1000 deve essere VALIDO (soglia strict < 1000)."""
    v = pbv.compute_verdict_pg(n_success=950, n_fallback_operational=50)
    assert v.code != "PERIODO INVALIDO"
    assert v.rate == pytest.approx(0.95)


def test_pg_verdict_ge_95pct_is_keep_confermato():
    """rate ≥ 95% → KEEP CONFERMATO."""
    v = pbv.compute_verdict_pg(n_success=4997, n_fallback_operational=3)
    assert v.code == "KEEP CONFERMATO"
    assert v.rate == pytest.approx(0.9994, abs=1e-4)


def test_pg_verdict_95pct_boundary_is_keep_confermato():
    """rate == 95% → KEEP CONFERMATO (>=95% inclusivo)."""
    v = pbv.compute_verdict_pg(n_success=4750, n_fallback_operational=250)
    assert v.code == "KEEP CONFERMATO"
    assert v.rate == pytest.approx(0.95)


def test_pg_verdict_in_90_95_is_osservazione_estesa():
    """90% ≤ rate < 95% → OSSERVAZIONE ESTESA."""
    v = pbv.compute_verdict_pg(n_success=4600, n_fallback_operational=400)
    assert v.code == "OSSERVAZIONE ESTESA"
    assert v.rate == pytest.approx(0.92)


def test_pg_verdict_90pct_boundary_is_osservazione_estesa():
    """rate == 90% → OSSERVAZIONE ESTESA (>=90% inclusivo per osservazione)."""
    v = pbv.compute_verdict_pg(n_success=4500, n_fallback_operational=500)
    assert v.code == "OSSERVAZIONE ESTESA"
    assert v.rate == pytest.approx(0.90)


def test_pg_verdict_below_90pct_is_alert_regressione():
    """rate < 90% → ALERT REGRESSIONE."""
    v = pbv.compute_verdict_pg(n_success=4250, n_fallback_operational=750)
    assert v.code == "ALERT REGRESSIONE"
    assert v.rate == pytest.approx(0.85)


def test_pg_verdict_zero_success_full_fallback_alert():
    """Zero success con sample sufficiente → ALERT REGRESSIONE (rate 0%)."""
    v = pbv.compute_verdict_pg(n_success=0, n_fallback_operational=1000)
    assert v.code == "ALERT REGRESSIONE"
    assert v.rate == pytest.approx(0.0)


def test_pg_verdict_zero_fallback_perfect_keep():
    """Zero fallback con sample sufficiente → KEEP CONFERMATO (rate 100%)."""
    v = pbv.compute_verdict_pg(n_success=5000, n_fallback_operational=0)
    assert v.code == "KEEP CONFERMATO"
    assert v.rate == pytest.approx(1.0)


# =============================================================================
# Trigger stratificazione per-tenant (§9.2 zona 5-30%)
# =============================================================================


def test_stratification_triggered_only_in_5_to_30_zone():
    """La stratificazione per-tenant si attiva SOLO quando MySQL verdict
    e' STRATIFICAZIONE PER-TENANT, non in altri casi (§9.2)."""
    # Zona stratificazione
    v = pbv.compute_verdict_mysql(n_success=75, n_fallback_disabled=425)
    assert pbv.mysql_needs_stratification(v) is True

    # Sotto 5% — no stratificazione, e' gia' proposta deprecazione
    v = pbv.compute_verdict_mysql(n_success=10, n_fallback_disabled=490)
    assert pbv.mysql_needs_stratification(v) is False

    # Sopra 30% — no stratificazione, e' KEEP
    v = pbv.compute_verdict_mysql(n_success=250, n_fallback_disabled=250)
    assert pbv.mysql_needs_stratification(v) is False

    # Periodo invalido — no stratificazione
    v = pbv.compute_verdict_mysql(n_success=5, n_fallback_disabled=10)
    assert pbv.mysql_needs_stratification(v) is False


# =============================================================================
# Costanti soglie — congelamento dei valori verbatim da §9.2/§9.3
# =============================================================================


def test_thresholds_match_kickoff_sections_verbatim():
    """I valori delle soglie sono copiati verbatim da kickoff.md §9.2/§9.3.
    Se un valore cambia qui, DEVE cambiare anche nella sezione del kickoff
    e viceversa. Questo test congela la corrispondenza."""
    # §9.2 MySQL
    assert pbv.MYSQL_MIN_SAMPLE == 100
    assert pbv.MYSQL_RATE_PROPOSAL_DEPRECATION == 0.05
    assert pbv.MYSQL_RATE_STRATIFICATION == 0.30
    # §9.3 PG
    assert pbv.PG_MIN_SAMPLE == 1000
    assert pbv.PG_RATE_KEEP_CONFIRMED == 0.95
    assert pbv.PG_RATE_OBSERVATION == 0.90
    # Stratificazione per-tenant (esclusione micro-tenant)
    assert pbv.STRATIFICATION_MIN_TENANT_SAMPLE == 10


# =============================================================================
# CLI date parsing (sanity — non esegue query)
# =============================================================================


def test_cli_parse_iso_date_accepts_date_only():
    assert pbv._parse_iso_date("2026-05-12") == "2026-05-12"


def test_cli_parse_iso_date_accepts_datetime():
    assert pbv._parse_iso_date("2026-05-12T00:00:00") == "2026-05-12T00:00:00"


def test_cli_parse_iso_date_rejects_invalid():
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        pbv._parse_iso_date("not-a-date")


# =============================================================================
# Output formatting smoke test (no DB, solo stringhe)
# =============================================================================


def test_format_mysql_section_keep_includes_rate_and_verdict():
    v = pbv.compute_verdict_mysql(n_success=300, n_fallback_disabled=200)
    out = pbv.format_mysql_section(
        "2026-05-12", "2026-05-26", 300, 200, v
    )
    assert "KEEP" in out
    assert "60.00%" in out
    assert "§9.2" in out


def test_format_pg_section_alert_includes_rca_hint():
    v = pbv.compute_verdict_pg(n_success=4250, n_fallback_operational=750)
    out = pbv.format_pg_section(
        "2026-05-12", "2026-05-26", 4250, 750, v
    )
    assert "ALERT REGRESSIONE" in out
    assert "RCA" in out
    assert "§9.3" in out


def test_format_stage_swap_section_zero_is_safe_message():
    out = pbv.format_stage_swap_section(n_dispatched=0, n_distinct_users=0)
    assert "safe" in out.lower()
    assert "nessun stage+swap" in out.lower()


def test_format_stage_swap_section_nonzero_is_attention_message():
    out = pbv.format_stage_swap_section(n_dispatched=15, n_distinct_users=3)
    assert "ATTENZIONE" in out
    assert "15" in out
    assert "3" in out
