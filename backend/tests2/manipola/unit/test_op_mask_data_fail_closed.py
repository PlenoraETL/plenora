"""[phase-2-err][batch5] γ1b (E5) — op_mask_data fail-closed privacy.

Audit Batch 5 §5.3 smell S3 (C1) — `op_mask_data` loop per-colonna aveva
`except Exception: logger.error([CRASH] ...) + continue`. Se la maschera
falliva su una colonna (regex rotta, mask_fn explode), il loop proseguiva
sulle colonne successive; `result` ritornato aveva la colonna fallita NON
mascherata → scrittura DB downstream = leak PII silenzioso.

Scenario privacy-critical (audit §5.3):
  Canvas mask_data [cf, email, phone]. Mask `cf` fallisce → `continue`.
  Output: cf in chiaro, email/phone mascherate → DB scrittura = leak PII.

Post-fix (γ1b — Opzione A fail-closed ratificata, RuntimeError):
  - `continue` → `raise RuntimeError(...) from e` dopo il
    logger.error([CRASH] ..., exc_info=True) che preserva str(e) tecnico
    lato server per postmortem admin.
  - Messaggio safe user-facing italiano: "Mask Data: mascheramento fallito
    per la colonna '{column}'. Operazione interrotta per prevenire leak
    PII parziale." — niente str(e) interpolato nel message.
  - Chain `__cause__` preservata via `from e`.
  - All-or-nothing: il raise avviene PRIMA di `return result`, quindi
    il `result` parzialmente mascherato non e' mai esposto al caller.
  - Nessuna nuova exception class: RuntimeError built-in (semantica
    "errore runtime mascheramento", non input utente).

Coerenza pattern:
  - Batch 5 E1 chiuso applica gia' fail-closed sui 32 siti transform
    top-level (E01-E32). γ1b estende privacy-safe al loop per-column.
  - Caller executor manipola gia' wrappato (E1 chiuso) → RuntimeError
    catturato + tradotto in error dict node pattern uniform.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from app.core.manipola.transforms.base import op_mask_data


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


# =============================================================================
# FAIL-CLOSED — mask_fn explode → raise RuntimeError (NO continue silent)
# =============================================================================


def test_op_mask_data_fails_closed_on_mask_fn_error():
    """Mock di MASK_FUNCTIONS['cf'] a raise RuntimeError → `op_mask_data`
    NON deve fare `continue` silent, ma `raise RuntimeError` con messaggio
    safe user-facing + chain `__cause__` preservata."""
    df = pd.DataFrame({
        "cf": ["ABCDEF12G34H567I", "LMNOPQ98R76S543T"],
        "email": ["a@b.com", "c@d.com"],
    })
    maskings = [
        {"column": "cf", "mask_type": "cf"},
        {"column": "email", "mask_type": "email"},
    ]

    # Forza la mask_fn 'cf' a sollevare
    def broken_cf_mask(v):
        raise RuntimeError("simulated: regex rotta su cf")

    with patch.dict(
        "app.core.manipola.transforms.base.MASK_FUNCTIONS",
        {"cf": broken_cf_mask},
        clear=False,
    ):
        with pytest.raises(RuntimeError) as exc_info:
            op_mask_data(df, maskings)

    msg = str(exc_info.value)
    # Messaggio safe user-facing italiano
    assert "Mask Data" in msg
    assert "colonna 'cf'" in msg
    assert "leak PII parziale" in msg
    assert "Operazione interrotta" in msg
    # Chain preservata via `from e`
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "simulated: regex rotta" in str(exc_info.value.__cause__)


def test_op_mask_data_no_partial_output_on_fail():
    """Test privacy-critical anti-regressione: su fail a metà del loop,
    `op_mask_data` NON deve ritornare un DataFrame parzialmente mascherato.
    Il raise avviene prima di `return result`, quindi il caller non riceve
    nessun output (no leak PII parziale).

    Forziamo fail sulla SECONDA colonna del loop per verificare che, anche
    se la prima colonna era stata mascherata ok in `result`, il raise
    interrompe prima del return (result NON viene esposto al caller)."""
    df = pd.DataFrame({
        "email": ["a@b.com", "c@d.com"],  # processata per prima
        "cf": ["ABCDEF12G34H567I", "LMNOPQ98R76S543T"],  # fallisce
    })
    maskings = [
        {"column": "email", "mask_type": "email"},
        {"column": "cf", "mask_type": "cf"},
    ]

    def broken_cf_mask(v):
        raise RuntimeError("boom sulla seconda")

    with patch.dict(
        "app.core.manipola.transforms.base.MASK_FUNCTIONS",
        {"cf": broken_cf_mask},
        clear=False,
    ):
        # Il caller NON deve ricevere un DataFrame parziale — la funzione
        # solleva prima del return. Quindi pytest.raises e' sufficiente:
        # non si arriva mai al punto in cui avremmo un result da ispezionare.
        with pytest.raises(RuntimeError, match="leak PII parziale"):
            op_mask_data(df, maskings)

    # Verifica invariante: il DataFrame originale `df` NON e' stato mutato.
    # `op_mask_data` fa `result = df.copy()` all'inizio, quindi df resta
    # inalterato. Questo e' il contratto privacy di base: il caller puo'
    # ritentare con config diverse sapendo che il suo df sorgente e' intatto.
    assert list(df.columns) == ["email", "cf"]
    assert df["cf"].tolist() == ["ABCDEF12G34H567I", "LMNOPQ98R76S543T"]
    assert df["email"].tolist() == ["a@b.com", "c@d.com"]


# =============================================================================
# HAPPY PATH — nessun raise, tutte colonne mascherate correttamente
# =============================================================================


def test_op_mask_data_happy_path_preserved():
    """Anti-regressione: tutte le maschere applicabili senza errori →
    `op_mask_data` ritorna DataFrame con colonne mascherate come da pattern
    pre-esistente (MASK_FUNCTIONS standard). Nessun raise."""
    df = pd.DataFrame({
        "email": ["alice@example.com", "bob@example.com"],
    })
    maskings = [{"column": "email", "mask_type": "email"}]

    result = op_mask_data(df, maskings, overwrite=False)

    # Colonna originale invariata, nuova colonna email_masked esiste
    assert "email" in result.columns
    assert "email_masked" in result.columns
    # I valori mascherati sono diversi dagli originali (maschera applicata)
    assert result["email"].tolist() == ["alice@example.com", "bob@example.com"]
    # I valori email_masked NON sono uguali agli originali
    assert result["email_masked"].tolist() != [
        "alice@example.com", "bob@example.com"
    ]
