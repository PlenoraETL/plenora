"""
Contract test — `op_join` pre-validation chiave mancante (2026-04-23).

Blinda: quando ``left_on`` o ``right_on`` non esiste nel DataFrame raw
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.manipola.transforms.joins import op_join


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_op_join_raises_with_raw_column_names_when_left_key_missing():
    """left_on non presente in left_df → KeyError con colonne raw.

    Riproduce il caso reale: left_df ha colonne `bl_*`, config chiede
    join su `pc_cd_id`. L'errore deve dire "pc_cd_id non esiste" +
    lista raw (bl_state, bl_zip, ...) SENZA suffisso _L.
    """
    left_df = pd.DataFrame({
        "bl_state": ["CA"],
        "bl_zip": ["12345"],
        "bl_pr_id": [1],
        "bl_bl_id": [10],
        "bl_name": ["foo"],
        "bl_address1": ["1 Main St"],
    })
    right_df = pd.DataFrame({"pc_cd_id": [1], "other": ["x"]})

    with pytest.raises(KeyError) as exc_info:
        op_join([left_df, right_df], left_on="pc_cd_id", right_on="pc_cd_id")

    msg = str(exc_info.value)

    # La chiave richiesta deve comparire nel messaggio
    assert "pc_cd_id" in msg
    # Almeno una colonna raw deve essere citata, senza suffisso _L
    assert "bl_state" in msg
    # Niente suffissi _L nel messaggio (anti-regression primario)
    assert "_L" not in msg, (
        f"Regressione: messaggio espone colonne post-rename con _L. "
        f"Il fix richiede pre-validation con nomi raw. Msg: {msg}"
    )
    # Hint diagnostico presente
    assert "config del nodo join" in msg or "upstream" in msg


def test_op_join_raises_with_raw_column_names_when_right_key_missing():
    """Simmetrico su right."""
    left_df = pd.DataFrame({"pc_cd_id": [1], "other": ["x"]})
    right_df = pd.DataFrame({
        "bl_state": ["CA"],
        "bl_zip": ["12345"],
    })

    with pytest.raises(KeyError) as exc_info:
        op_join([left_df, right_df], left_on="pc_cd_id", right_on="pc_cd_id")

    msg = str(exc_info.value)

    assert "pc_cd_id" in msg
    assert "bl_state" in msg
    assert "_R" not in msg, (
        f"Regressione: messaggio espone colonne post-rename con _R. Msg: {msg}"
    )


def test_op_join_happy_path_preserves_join_key():
    """Non-regression: con chiavi presenti, op_join esegue normalmente
    e preserva la chiave nel risultato (senza suffisso)."""
    left_df = pd.DataFrame({"id": [1, 2, 3], "left_val": ["a", "b", "c"]})
    right_df = pd.DataFrame({"id": [1, 2, 3], "right_val": ["x", "y", "z"]})

    result = op_join([left_df, right_df], left_on="id", right_on="id", how="inner")

    assert len(result) == 3
    # La chiave resta "id" (senza _L ne' _R)
    assert "id" in result.columns
    # I non-key assumono suffisso canonico
    assert "left_val_L" in result.columns
    assert "right_val_R" in result.columns


def test_op_join_internal_guard_message_distinct_from_user_error():
    """Il messaggio pre-rename (utente) e post-rename (internal) devono
    essere distinguibili a livello di prefisso/testo: un log review deve
    capire immediatamente se e' un errore di config utente o una
    regressione interna di safe_rename."""
    left_df = pd.DataFrame({"bl_x": [1]})
    right_df = pd.DataFrame({"id": [1]})

    with pytest.raises(KeyError) as exc_info:
        op_join([left_df, right_df], left_on="pc_cd_id", right_on="id")

    # User-facing error: NON deve contenere il marker [internal]
    assert "[internal]" not in str(exc_info.value), (
        "L'errore user-facing (pre-rename) non deve essere marcato [internal]; "
        "il marker e' riservato al guard post-safe_rename."
    )
