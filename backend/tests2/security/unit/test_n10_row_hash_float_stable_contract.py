"""Contract test N10 — row_hash MD5 stabile su float / Decimal.

Pre-fix scenario:
  `_normalize_value(float)` usava `str(v)` crudo. Due float semanticamente
  uguali ma prodotti da path diversi (es. lettura ripetuta da Postgres con
  arrotondamenti minimi, o aritmetica che introduce errore di floating
  point) davano output diverso:
      str(1.23)          == "1.23"
      str(1.2300000001)  == "1.2300000001"
  -> MD5 row_hash diverso -> dedup row-level rotto -> righe "uguali"
  venivano considerate distinte, causando duplicati invisibili nella
  pipeline.

Fix (2026-04-17): helper `_format_float_stable(v)` usa
`f"{v:.6f}".rstrip('0').rstrip('.')`:
  - 6 decimali fissi (precisione ragionevole per ETL tipico)
  - strip zeri trailing e punto -> output canonico
  - NaN/Inf gestiti esplicitamente
  - coerente tra float e Decimal (entrambi passano per l'helper)

Questo contract congela:
  1. float vicini semanticamente uguali -> stesso hash
  2. int N e float N.0 -> stesso output normalizzato (coerenza)
  3. Decimal(x) e float(x) -> stesso output
  4. NaN / Inf gestiti in modo deterministico
"""

from __future__ import annotations

import decimal
import math

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# =============================================================================
# 1. Float: noise sotto-microscopico -> stesso hash
# =============================================================================


@pytest.mark.parametrize("a,b", [
    (1.23, 1.2300000001),
    (0.1 + 0.2, 0.3),           # classico esempio di FP noise
    (1e-7 + 1.0, 1.0),          # noise oltre la 6a cifra decimale
    (-1.23, -1.2300000001),
])
def test_float_noise_collapses_to_same_normalized_output(a, b):
    """Due float quasi-uguali producono lo stesso output normalizzato
    -> dedup row_hash funziona."""
    from app.core.intermediate.writer import _format_float_stable
    assert _format_float_stable(a) == _format_float_stable(b), (
        f"{a} e {b} producono output diversi: "
        f"'{_format_float_stable(a)}' vs '{_format_float_stable(b)}'"
    )


def test_float_distant_values_produce_different_output():
    """Sanity: float realmente diversi NON collassano (tolleranza a 6 decimali)."""
    from app.core.intermediate.writer import _format_float_stable
    assert _format_float_stable(1.23) != _format_float_stable(1.24)
    assert _format_float_stable(1.234568) != _format_float_stable(1.234569)


# =============================================================================
# 2. Coerenza int/float
# =============================================================================


@pytest.mark.parametrize("n", [0, 1, 100, -42, 1000000])
def test_int_and_float_same_value_produce_same_normalized_output(n):
    """_normalize_value(100) e _normalize_value(100.0) -> stessa stringa
    -> stessa row_hash. Evita duplicati dovuti solo a diversita' di tipo."""
    from app.core.intermediate.writer import _normalize_value
    assert _normalize_value(n) == _normalize_value(float(n)), (
        f"int {n} e float {float(n)} normalizzati differenti"
    )


# =============================================================================
# 3. Decimal coerente con float
# =============================================================================


@pytest.mark.parametrize("v", ["1.23", "0.1", "100.5", "-42.0"])
def test_decimal_and_float_produce_same_normalized_output(v):
    """Decimal(x) e float(x) -> stesso output. Prima era `str(float(v))`
    crudo che soffriva lo stesso bug di _format_float_stable originale."""
    from app.core.intermediate.writer import _normalize_value
    d = decimal.Decimal(v)
    f = float(v)
    assert _normalize_value(d) == _normalize_value(f)


# =============================================================================
# 4. NaN / Inf deterministici
# =============================================================================


def test_nan_produces_stable_output():
    from app.core.intermediate.writer import _format_float_stable
    # NaN stesso se stesso -> stringa costante
    assert _format_float_stable(float("nan")) == "nan"


def test_positive_inf_produces_stable_output():
    from app.core.intermediate.writer import _format_float_stable
    assert _format_float_stable(float("inf")) == "+inf"


def test_negative_inf_produces_stable_output():
    from app.core.intermediate.writer import _format_float_stable
    assert _format_float_stable(float("-inf")) == "-inf"


# =============================================================================
# 5. Output canonico: zeri trailing e punto finale rimossi
# =============================================================================


@pytest.mark.parametrize("v,expected", [
    (1.0, "1"),
    (0.0, "0"),
    (1.23, "1.23"),
    (1.100, "1.1"),
    (-1.0, "-1"),
    (1.234567891, "1.234568"),   # arrotondamento a 6 decimali
])
def test_canonical_output_no_trailing_zeros_or_dot(v, expected):
    from app.core.intermediate.writer import _format_float_stable
    assert _format_float_stable(v) == expected


# =============================================================================
# 6. row_hash end-to-end: dedup funziona
# =============================================================================


def test_row_hash_stable_across_floating_point_noise():
    """compute_row_hash produce lo stesso MD5 per righe con float vicini."""
    from app.core.intermediate.writer import compute_row_hash

    row1 = {"prezzo": 1.23, "quantita": 100}
    row2 = {"prezzo": 1.2300000001, "quantita": 100}

    h1 = compute_row_hash(row1, ["prezzo", "quantita"])
    h2 = compute_row_hash(row2, ["prezzo", "quantita"])
    assert h1 == h2, "Float noise rompe la dedup row_hash"


def test_row_hash_still_distinguishes_different_values():
    """Sanity: righe realmente diverse hanno hash diversi."""
    from app.core.intermediate.writer import compute_row_hash

    row1 = {"prezzo": 1.23, "quantita": 100}
    row2 = {"prezzo": 1.24, "quantita": 100}

    h1 = compute_row_hash(row1, ["prezzo", "quantita"])
    h2 = compute_row_hash(row2, ["prezzo", "quantita"])
    assert h1 != h2


# =============================================================================
# 7. Policy esplicita: coarse quantization at 6 decimals
# =============================================================================
# Il fix N10 non e' "semanticamente neutro": sceglie una tolleranza
# applicativa di 6 decimali. Questi test la nominano esplicitamente
# perche' e' una SCELTA di business (normalizzazione per dedup stabile),
# NON una correzione matematica perfetta. Se un progetto ha bisogno di
# precision > 6 decimali (es. coordinate GPS a 1cm, hash molecolari),
# usa WKT o string rappresentazione custom, non il float hash.


def test_float_trailing_zeros_ignored_in_row_hash():
    """1.23 e 1.230000 producono lo stesso row_hash (trailing zeros
    canonicalizzati via rstrip). Coerente col concept di uguaglianza
    numerica."""
    from app.core.intermediate.writer import compute_row_hash

    # NB: in Python 1.23 == 1.230000 come float; questo test documenta
    # che la normalizzazione preserva questa uguaglianza nell'hash.
    h1 = compute_row_hash({"x": 1.23}, ["x"])
    h2 = compute_row_hash({"x": 1.230000}, ["x"])
    assert h1 == h2


def test_policy_coarse_quantization_at_6_decimals_collapses_7th_digit():
    """Due float che differiscono SOLO alla 7a cifra decimale producono
    lo stesso hash — collasso voluto dalla policy di quantizzazione a
    6 decimali. NON e' correttezza matematica: e' la scelta applicativa
    per avere dedup stabile su floating-point noise.

    Se in futuro un dominio richiede precisione > 6 dec (es. coordinate
    GPS sub-centimetriche), questo test falliva volontariamente e
    l'implementazione va revisionata (aumentare precision oppure
    rappresentazione custom per quelle colonne)."""
    from app.core.intermediate.writer import compute_row_hash

    h1 = compute_row_hash({"x": 1.2300001}, ["x"])
    h2 = compute_row_hash({"x": 1.2300002}, ["x"])
    assert h1 == h2, (
        "Policy coarse 6-dec: valori che differiscono oltre la 6a cifra "
        "decimale DEVONO collassare nello stesso hash. Se questo test "
        "fallisce, la precision della normalizzazione e' stata cambiata."
    )


def test_policy_6_decimals_distinguishes_at_6th_digit():
    """Sanity inverso: due float che differiscono dentro la 6a cifra
    decimale producono hash DIVERSI. Conferma che la policy non sta
    collassando valori distinti al di sotto della soglia."""
    from app.core.intermediate.writer import compute_row_hash

    h1 = compute_row_hash({"x": 1.234567}, ["x"])
    h2 = compute_row_hash({"x": 1.234568}, ["x"])  # differenza alla 6a cifra
    assert h1 != h2, (
        "La policy 6-dec deve distinguere valori alla 6a cifra decimale. "
        "Se questo test fallisce, la precision e' stata ridotta sotto 6 dec."
    )
