"""
Contract test: sampled fingerprint per checkpoint snapshots.

Previous behavior: `compute_dataframe_fingerprint` faceva
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.intermediate, pytest.mark.contract]


def _patch_settings(**overrides):
    from app.core.intermediate import policy as _policy
    from app.core.intermediate.policy import reset_validation_cache

    cfg = {
        "mode": "metadata_only",
        "preview_rows": 500,
        "artifact_format": "parquet",
        "manipola_checkpoints": "critical",
        "estrai_checkpoints": "final_only",
        "arricchisci_checkpoints": "final_only",
        "artifact_bucket": None,
        "artifact_prefix": "intermediate",
        "fingerprint_mode": "sample",
        "fingerprint_sample_rows": 1000,
    }
    cfg.update(overrides)
    reset_validation_cache()
    return patch.object(_policy, "_read_settings", return_value=cfg)


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    from app.core.intermediate.policy import reset_validation_cache
    reset_validation_cache()
    yield
    reset_validation_cache()


# A. Settings validation


def test_validate_settings_rejects_unknown_fingerprint_mode():
    from app.core.intermediate.policy import (
        IntermediateSettingsError, validate_intermediate_settings,
    )
    cfg = {
        "mode": "metadata_only", "preview_rows": 500,
        "artifact_format": "parquet",
        "manipola_checkpoints": "critical",
        "estrai_checkpoints": "final_only",
        "arricchisci_checkpoints": "final_only",
        "artifact_bucket": None, "artifact_prefix": "intermediate",
        "fingerprint_mode": "abracadabra",
        "fingerprint_sample_rows": 1000,
    }
    with pytest.raises(IntermediateSettingsError, match="FINGERPRINT_MODE"):
        validate_intermediate_settings(cfg)


def test_validate_settings_rejects_zero_fingerprint_sample_rows():
    from app.core.intermediate.policy import (
        IntermediateSettingsError, validate_intermediate_settings,
    )
    cfg = {
        "mode": "metadata_only", "preview_rows": 500,
        "artifact_format": "parquet",
        "manipola_checkpoints": "critical",
        "estrai_checkpoints": "final_only",
        "arricchisci_checkpoints": "final_only",
        "artifact_bucket": None, "artifact_prefix": "intermediate",
        "fingerprint_mode": "sample",
        "fingerprint_sample_rows": 0,
    }
    with pytest.raises(IntermediateSettingsError, match="> 0"):
        validate_intermediate_settings(cfg)


def test_validate_settings_accepts_full_mode():
    """`mode=full` e' una mode legittima per audit forte."""
    from app.core.intermediate.policy import validate_intermediate_settings
    cfg = {
        "mode": "metadata_only", "preview_rows": 500,
        "artifact_format": "parquet",
        "manipola_checkpoints": "critical",
        "estrai_checkpoints": "final_only",
        "arricchisci_checkpoints": "final_only",
        "artifact_bucket": None, "artifact_prefix": "intermediate",
        "fingerprint_mode": "full",
        "fingerprint_sample_rows": 1000,
    }
    validate_intermediate_settings(cfg)  # no raise


# B. Determinismo + dispatch algoritmo


def test_fingerprint_sample_returns_pandas_sample_hash_v1():
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df = pd.DataFrame({"a": [1, 2, 3]})
    with _patch_settings(fingerprint_mode="sample"):
        h, algo = compute_dataframe_fingerprint(df)
    assert algo == "pandas_sample_hash_v1"
    assert len(h) == 64


def test_fingerprint_full_returns_pandas_hash_v1():
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df = pd.DataFrame({"a": [1, 2, 3]})
    with _patch_settings(fingerprint_mode="full"):
        h, algo = compute_dataframe_fingerprint(df)
    assert algo == "pandas_hash_v1"
    assert len(h) == 64


def test_fingerprint_sample_deterministic_on_same_df():
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df = pd.DataFrame({"a": list(range(2000)), "b": ["v"] * 2000})
    with _patch_settings(fingerprint_mode="sample", fingerprint_sample_rows=500):
        h1, _ = compute_dataframe_fingerprint(df.copy())
        h2, _ = compute_dataframe_fingerprint(df.copy())
    assert h1 == h2


# C. Sensibilita' schema/head/len


def test_fingerprint_sample_changes_when_schema_changes():
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df_a = pd.DataFrame({"a": [1, 2, 3]})
    df_b = pd.DataFrame({"b": [1, 2, 3]})  # diversa colonna
    with _patch_settings(fingerprint_mode="sample"):
        h_a, _ = compute_dataframe_fingerprint(df_a)
        h_b, _ = compute_dataframe_fingerprint(df_b)
    assert h_a != h_b


def test_fingerprint_sample_changes_when_dtype_changes():
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df_int = pd.DataFrame({"a": pd.Series([1, 2, 3], dtype="int64")})
    df_float = pd.DataFrame({"a": pd.Series([1.0, 2.0, 3.0], dtype="float64")})
    with _patch_settings(fingerprint_mode="sample"):
        h_int, _ = compute_dataframe_fingerprint(df_int)
        h_float, _ = compute_dataframe_fingerprint(df_float)
    assert h_int != h_float


def test_fingerprint_sample_changes_on_first_n_row_modification():
    """Modifica nelle prime N righe -> hash diverso (sensibilita' attesa)."""
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df_a = pd.DataFrame({"a": list(range(2000))})
    df_b = df_a.copy()
    df_b.iloc[5, 0] = 9999  # modifica entro head(1000)
    with _patch_settings(fingerprint_mode="sample", fingerprint_sample_rows=1000):
        h_a, _ = compute_dataframe_fingerprint(df_a)
        h_b, _ = compute_dataframe_fingerprint(df_b)
    assert h_a != h_b


def test_fingerprint_sample_changes_on_row_count_change_even_if_sample_identical():
    """Cambio cardinalita' totale: anche se le prime N righe sono identiche,
    l'hash cambia perche' `len(df)` e' nel digest. Garantisce che il
    sample mode non perda mai cambi di cardinalita' (caso comune
    post-filter / dedup)."""
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df_a = pd.DataFrame({"a": list(range(2000))})
    df_b = pd.DataFrame({"a": list(range(2500))})  # head(1000) identico, len diverso
    with _patch_settings(fingerprint_mode="sample", fingerprint_sample_rows=1000):
        h_a, _ = compute_dataframe_fingerprint(df_a)
        h_b, _ = compute_dataframe_fingerprint(df_b)
    assert h_a != h_b


# D. Limite documentato del sample


def test_fingerprint_sample_may_miss_changes_outside_sample_window():
    """LIMITE DOCUMENTATO: cambi che toccano SOLO righe oltre la N-esima
    e che NON cambiano la cardinalita' totale POSSONO non essere
    rilevati. Questo test pinna il trade-off: cambiare un valore alla
    riga 1500 con sample_rows=1000 non cambia l'hash.

    Audit forte richiede `INTERMEDIATE_FINGERPRINT_MODE=full`."""
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df_a = pd.DataFrame({"a": list(range(2000))})
    df_b = df_a.copy()
    df_b.iloc[1500, 0] = 999999  # modifica oltre head(1000)
    with _patch_settings(fingerprint_mode="sample", fingerprint_sample_rows=1000):
        h_a, _ = compute_dataframe_fingerprint(df_a)
        h_b, _ = compute_dataframe_fingerprint(df_b)
    # Tradeoff documentato: hash uguale anche se i dati differiscono
    # nelle righe oltre il sample.
    assert h_a == h_b


def test_fingerprint_full_does_detect_changes_outside_sample_window():
    """Counterpart: in mode `full` la modifica oltre head(1000) viene
    sempre rilevata. Conferma che mode=full e' la via per audit forte."""
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df_a = pd.DataFrame({"a": list(range(2000))})
    df_b = df_a.copy()
    df_b.iloc[1500, 0] = 999999
    with _patch_settings(fingerprint_mode="full"):
        h_a, _ = compute_dataframe_fingerprint(df_a)
        h_b, _ = compute_dataframe_fingerprint(df_b)
    assert h_a != h_b


# E. Niente df.to_dict full


def test_fingerprint_sample_does_not_call_to_dict_full():
    """Anti-regression: l'algoritmo sample NON deve invocare
    `df.to_dict(orient='records')` su tutto il DataFrame. Verifica
    via spy: `to_dict` non viene mai chiamato durante il fingerprint."""
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    from unittest.mock import MagicMock

    real_df = pd.DataFrame({"a": list(range(5000))})

    # Wrap real DataFrame con spy su to_dict.
    spy_to_dict = MagicMock(side_effect=AssertionError(
        "to_dict NON deve essere chiamato dal fingerprint sample"
    ))
    with patch.object(pd.DataFrame, "to_dict", spy_to_dict):
        with _patch_settings(fingerprint_mode="sample", fingerprint_sample_rows=1000):
            compute_dataframe_fingerprint(real_df)

    spy_to_dict.assert_not_called()


# F. Performance smoke


def test_fingerprint_sample_is_fast_on_large_dataframe():
    """Smoke perf: 100k righe x 10 cols, sample mode 1000 righe.
    Atteso: < 200 ms (margine ampio sul valore atteso ~10–30 ms).
    Senza sample mode (full) sarebbe diversi secondi."""
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint

    n_rows = 100_000
    df = pd.DataFrame({
        f"c{i}": list(range(n_rows)) for i in range(10)
    })
    with _patch_settings(fingerprint_mode="sample", fingerprint_sample_rows=1000):
        t0 = time.monotonic()
        compute_dataframe_fingerprint(df)
        elapsed = time.monotonic() - t0

    # 200 ms cap: include overhead pytest/python.
    assert elapsed < 0.2, (
        f"sample fingerprint troppo lento: {elapsed:.3f}s su {n_rows} righe"
    )


# G. Sample size influenza il digest


def test_fingerprint_sample_size_influences_digest():
    """Cambiando `fingerprint_sample_rows` fra run l'hash diventa
    diverso (il digest include il sample size). Documenta che
    confrontare hash fra deployment con sample_rows differenti non e'
    apple-to-apple."""
    from app.core.intermediate.snapshot import compute_dataframe_fingerprint
    df = pd.DataFrame({"a": list(range(2000))})
    with _patch_settings(fingerprint_mode="sample", fingerprint_sample_rows=500):
        h_500, _ = compute_dataframe_fingerprint(df)
    with _patch_settings(fingerprint_mode="sample", fingerprint_sample_rows=1000):
        h_1000, _ = compute_dataframe_fingerprint(df)
    assert h_500 != h_1000
