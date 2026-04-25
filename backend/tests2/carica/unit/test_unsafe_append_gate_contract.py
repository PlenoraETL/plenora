"""Contract test P1-05 / F-DATA-08 — gate fail-loud su combo unsafe append.

Scope:
  Fissa il rifiuto esplicito della combo rischiosa
    write_mode=APPEND + transaction_mode=PER_BATCH + match_columns=[]
  quando `allow_unsafe_append=False` (default).

  Pre-fix il writer emetteva solo `logger.warning` e procedeva: in
  produzione il warning e' di fatto silent, i retry parziali (network
  glitch, OOM mid-batch) duplicavano righe sulla destinazione. Vedi
  `AUDIT_REPORT_data.md` F-DATA-08 e `orchestrate/phase_3_audit/TRIAGE.md`
  P1-05.

  Fix: la combo solleva `ValueError` con messaggio diagnostico che
  elenca le tre alternative:
    (1) transaction_mode=all_or_nothing (rollback completo)
    (2) configurare match_columns per conflict detection
    (3) settare allow_unsafe_append=true consapevolmente

Sezioni:
  1. Combo unsafe senza opt-in -> ValueError con detail informativo
  2. Combo unsafe con opt-in (allow_unsafe_append=True) -> proceed + warning
  3. Combo sicure non gated (ALL_OR_NOTHING / match_columns / non-APPEND)
  4. WriteConfig.from_node_data default-safe + opt-in dal canvas

Test in-memory: nessun DB live, nessun engine reale. Il writer viene
istanziato con un MagicMock engine e il check unsafe-combo scatta
PRIMA di qualsiasi operazione DB.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.models import (
    ConflictPolicy,
    TransactionMode,
    WriteConfig,
    WriteMode,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.carica,
    pytest.mark.contract,
    pytest.mark.regression,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_writer(config: WriteConfig) -> DatabaseWriter:
    """Costruisce un DatabaseWriter con engine fake.

    Il gate unsafe-combo e' valutato prima di toccare l'engine, quindi
    bastando un MagicMock evitiamo qualsiasi connessione reale.
    """
    return DatabaseWriter(
        config=config,
        engine=MagicMock(),
        db_type="postgres",
    )


def _df() -> pd.DataFrame:
    """DataFrame minimale non-vuoto: il gate scatta solo se ci sono righe
    da scrivere (write su df vuoto ritorna prima del check)."""
    return pd.DataFrame([{"id": 1, "amount": 99.5}])


# ---------------------------------------------------------------------------
# 1. Combo unsafe senza opt-in -> ValueError con messaggio diagnostico
# ---------------------------------------------------------------------------

def test_unsafe_append_combo_without_flag_raises():
    """APPEND + PER_BATCH + match_columns=[] + allow_unsafe_append=False
    -> ValueError esplicito con elenco delle 3 alternative."""
    config = WriteConfig(
        connection_id="conn",
        table_name="dest",
        write_mode=WriteMode.APPEND,
        transaction_mode=TransactionMode.PER_BATCH,
        match_columns=[],
        allow_unsafe_append=False,
    )
    writer = _make_writer(config)

    with pytest.raises(ValueError) as exc:
        writer.write(_df())

    msg = str(exc.value)
    assert "[UNSAFE WRITE CONFIG]" in msg
    # Le 3 alternative devono essere chiaramente elencate nel messaggio.
    assert "all_or_nothing" in msg, "Alt. 1 (rollback completo) deve apparire"
    assert "match_columns" in msg, "Alt. 2 (conflict detection) deve apparire"
    assert "allow_unsafe_append" in msg, "Alt. 3 (opt-in consapevole) deve apparire"


def test_unsafe_append_error_default_when_flag_unset():
    """Default sicuro: chi non setta `allow_unsafe_append` riceve
    fail-loud. Regressione-guard: se qualcuno cambia il default a True,
    questo test fallisce immediatamente."""
    cfg = WriteConfig(connection_id="conn", table_name="dest")
    assert cfg.allow_unsafe_append is False, (
        "REGRESSIONE: default di allow_unsafe_append cambiato. "
        "Default sicuro = False; True abilita silent corruption su retry."
    )


# ---------------------------------------------------------------------------
# 2. Combo unsafe con opt-in -> proceed + warning
# ---------------------------------------------------------------------------

def test_unsafe_append_combo_with_flag_proceeds(monkeypatch):
    """`allow_unsafe_append=True` consente la combo + emette warning.

    Verifichiamo che la write proceda oltre il gate (raggiunge
    `_prepare_dataframe`) e che il logger emetta un warning informativo.
    """
    from app.core.carica import database_writer as dw_mod

    config = WriteConfig(
        connection_id="conn",
        table_name="dest",
        write_mode=WriteMode.APPEND,
        transaction_mode=TransactionMode.PER_BATCH,
        match_columns=[],
        allow_unsafe_append=True,
    )
    writer = _make_writer(config)

    # Spy sul logger del modulo: dobbiamo vedere il warning informativo.
    spy_logger = MagicMock()
    monkeypatch.setattr(dw_mod, "logger", spy_logger)

    # Stub `_prepare_dataframe` per fermarsi prima delle operazioni DB:
    # confermiamo che il flow ha superato il gate raggiungendo questo step.
    # KeyboardInterrupt e' BaseException, NON viene catturato dal
    # `try/except Exception` di `write()`: usiamo questo come sentinel
    # per fermarci esattamente dopo aver superato il gate.
    def _stop_after_gate(self, df):
        raise KeyboardInterrupt("gate passed")

    monkeypatch.setattr(DatabaseWriter, "_prepare_dataframe", _stop_after_gate)

    with pytest.raises(KeyboardInterrupt, match="gate passed"):
        writer.write(_df())

    # Warning informativo emesso (opt-in path).
    assert spy_logger.warning.called, (
        "Atteso warning [UNSAFE WRITE CONFIG opt-in] quando "
        "allow_unsafe_append=True"
    )
    warning_msg = spy_logger.warning.call_args[0][0]
    assert "opt-in" in warning_msg
    assert "allow_unsafe_append=True" in warning_msg


# ---------------------------------------------------------------------------
# 3. Combo sicure non gated
# ---------------------------------------------------------------------------

def test_safe_combo_all_or_nothing_not_gated(monkeypatch):
    """APPEND + ALL_OR_NOTHING + match_columns=[] e' sicura: rollback
    completo su fail. Il gate non deve scattare."""
    config = WriteConfig(
        connection_id="conn",
        table_name="dest",
        write_mode=WriteMode.APPEND,
        transaction_mode=TransactionMode.ALL_OR_NOTHING,
        match_columns=[],
    )
    writer = _make_writer(config)

    monkeypatch.setattr(
        DatabaseWriter, "_prepare_dataframe",
        lambda self, df: (_ for _ in ()).throw(KeyboardInterrupt("gate not raised")),
    )

    # Niente ValueError: passiamo il gate e arriviamo a _prepare_dataframe.
    with pytest.raises(KeyboardInterrupt, match="gate not raised"):
        writer.write(_df())


def test_safe_combo_with_match_columns_not_gated(monkeypatch):
    """APPEND + PER_BATCH + match_columns=[id] e' sicura: conflict
    detection previene duplicati su retry."""
    config = WriteConfig(
        connection_id="conn",
        table_name="dest",
        write_mode=WriteMode.APPEND,
        transaction_mode=TransactionMode.PER_BATCH,
        match_columns=["id"],
    )
    writer = _make_writer(config)

    monkeypatch.setattr(
        DatabaseWriter, "_prepare_dataframe",
        lambda self, df: (_ for _ in ()).throw(KeyboardInterrupt("gate not raised")),
    )

    with pytest.raises(KeyboardInterrupt, match="gate not raised"):
        writer.write(_df())


def test_non_append_modes_not_gated(monkeypatch):
    """Il gate riguarda solo `WriteMode.APPEND`. UPDATE / UPSERT / CREATE
    non sono toccati anche se transaction_mode=PER_BATCH e match_columns=[].
    """
    monkeypatch.setattr(
        DatabaseWriter, "_prepare_dataframe",
        lambda self, df: (_ for _ in ()).throw(KeyboardInterrupt("gate not raised")),
    )

    for mode in (WriteMode.CREATE, WriteMode.REPLACE):
        config = WriteConfig(
            connection_id="conn",
            table_name="dest",
            write_mode=mode,
            transaction_mode=TransactionMode.PER_BATCH,
            match_columns=[],
        )
        writer = _make_writer(config)
        with pytest.raises(KeyboardInterrupt, match="gate not raised"):
            writer.write(_df())


# ---------------------------------------------------------------------------
# 4. WriteConfig.from_node_data: default-safe + opt-in dal canvas
# ---------------------------------------------------------------------------

def test_from_node_data_default_allow_unsafe_append_false():
    """Canvas senza chiave esplicita -> default sicuro (False).

    Tutti i progetti esistenti deserializzati dal canvas non sono
    impattati: il default rimane "rifiuta combo unsafe" finche' non
    viene esplicitamente abilitato.
    """
    cfg = WriteConfig.from_node_data({
        "connection_id": "conn",
        "table_name": "dest",
        "write_mode": "append",
        "transaction_mode": "per_batch",
    })
    assert cfg.allow_unsafe_append is False


def test_from_node_data_propagates_explicit_opt_in():
    """Canvas con `allow_unsafe_append: true` -> WriteConfig.allow_unsafe_append=True."""
    cfg = WriteConfig.from_node_data({
        "connection_id": "conn",
        "table_name": "dest",
        "write_mode": "append",
        "transaction_mode": "per_batch",
        "allow_unsafe_append": True,
    })
    assert cfg.allow_unsafe_append is True


def test_to_dict_roundtrip_preserves_allow_unsafe_append():
    """`to_dict` espone il flag cosi' il canvas serializzato lo persiste,
    e `from_node_data` lo rilegge identico (round-trip)."""
    original = WriteConfig(
        connection_id="conn",
        table_name="dest",
        write_mode=WriteMode.APPEND,
        transaction_mode=TransactionMode.PER_BATCH,
        match_columns=[],
        allow_unsafe_append=True,
    )
    dumped = original.to_dict()
    assert dumped["allow_unsafe_append"] is True

    rebuilt = WriteConfig.from_node_data(dumped)
    assert rebuilt.allow_unsafe_append is True


# ---------------------------------------------------------------------------
# 5. Strict boolean su from_node_data (review Step 3 review #2)
# ---------------------------------------------------------------------------
#
# Il flag abilita una modalita' unsafe: SOLO il boolean True puo' abilitare
# l'opt-in. Stringhe "false"/"0"/"true"/"1" e altri truthy-Python (es. lista
# non vuota, int diverso da 0) NON devono attivarlo: `bool("false")` e'
# True in Python e abiliterebbe silenziosamente la combo rischiosa.

def test_from_node_data_string_false_stays_false():
    """Stringa "false" -> NON abilita l'opt-in.

    `bool("false")` e' True in Python; serve identity-check `is True`."""
    cfg = WriteConfig.from_node_data({
        "connection_id": "conn",
        "allow_unsafe_append": "false",
    })
    assert cfg.allow_unsafe_append is False


def test_from_node_data_string_zero_stays_false():
    """Stringa "0" -> NON abilita l'opt-in."""
    cfg = WriteConfig.from_node_data({
        "connection_id": "conn",
        "allow_unsafe_append": "0",
    })
    assert cfg.allow_unsafe_append is False


def test_from_node_data_string_true_stays_false():
    """Stringa "true" -> NON abilita l'opt-in.

    Forziamo il frontend a inviare il boolean nativo: una stringa "true"
    e' un bug del client che NON deve avere effetto silenzioso.
    """
    cfg = WriteConfig.from_node_data({
        "connection_id": "conn",
        "allow_unsafe_append": "true",
    })
    assert cfg.allow_unsafe_append is False


def test_from_node_data_boolean_true_enables_opt_in():
    """Solo il boolean Python `True` abilita l'opt-in (path canonico)."""
    cfg = WriteConfig.from_node_data({
        "connection_id": "conn",
        "allow_unsafe_append": True,
    })
    assert cfg.allow_unsafe_append is True
