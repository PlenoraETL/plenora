"""
[phase-2-err][batch8] C4 — trusted_roots build fail-closed + breadcrumb.

Audit Batch 8 §3.1 S5 / fix F5: `resolver._materialize_from_output_path`
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.core.artifacts.resolver import ArtifactResolver
from app.core.artifacts.types import ArtifactResolutionError, RunArtifactRef


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# PR #34 P1-B: pattern B2.0 modernization. Sostituisce
# `with patch("app.core.artifacts.resolver.logger") as mock_logger`
# (test logger-bound fragile) con un handler stdlib agganciato al
# logger reale. `StructuredLogger` ha `propagate=False`, quindi
# `caplog` non funziona out-of-the-box: usiamo un handler custom
# clone-da `tests2/security/unit/test_n4_formula_validator_contract.py:157`.
class _ListHandler(logging.Handler):
    """Handler custom: colleziona LogRecord in lista. Aggira il
    logger custom con propagate=False (caplog non arriva al target)."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _attach_handler(logger_name: str):
    """Attacca un _ListHandler al logger stdlib indicato. Ritorna
    (handler, target_logger): caller deve chiamare
    `target_logger.removeHandler(handler)` nel finally."""
    target = logging.getLogger(logger_name)
    handler = _ListHandler()
    target.addHandler(handler)
    return handler, target


_RESOLVER_LOGGER = "app.core.artifacts.resolver"


@pytest.fixture
def resolver():
    return ArtifactResolver()


# HAPPY PATH — tutti i setting validi / skip legittimi


def test_trusted_roots_built_with_all_three_settings(resolver, monkeypatch, tmp_path):
    """RESULTS_DIR+TEMP_DIR+DATA_DIR tutti validi → path legit dentro
    RESULTS_DIR/proj viene accettato (3 trusted_roots)."""
    from app import config as _cfg

    results_dir = tmp_path / "results"
    (results_dir / "proj-1").mkdir(parents=True)
    target = results_dir / "proj-1" / "out.csv"
    target.write_text("id\n1\n", encoding="utf-8")

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    data_dir = tmp_path / "data"
    (data_dir / "temp").mkdir(parents=True)

    fake_settings = SimpleNamespace(
        RESULTS_DIR=results_dir,
        TEMP_DIR=temp_dir,
        DATA_DIR=data_dir,
    )
    monkeypatch.setattr(_cfg, "settings", fake_settings, raising=False)

    ref = RunArtifactRef(project_id="proj-1", output_path=str(target))
    m = resolver.materialize_run(ref)
    assert m.path == target.resolve()


def test_trusted_roots_temp_and_data_none_skip_legit(resolver, monkeypatch, tmp_path):
    """Solo RESULTS_DIR valido, TEMP_DIR=None, DATA_DIR=None → path legit
    accettato. `if not base: continue` skippa senza emettere warning."""
    from app import config as _cfg

    results_dir = tmp_path / "results"
    (results_dir / "proj-1").mkdir(parents=True)
    target = results_dir / "proj-1" / "out.csv"
    target.write_text("id\n1\n", encoding="utf-8")

    fake_settings = SimpleNamespace(
        RESULTS_DIR=results_dir,
        TEMP_DIR=None,
        DATA_DIR=None,
    )
    monkeypatch.setattr(_cfg, "settings", fake_settings, raising=False)

    handler, target_logger = _attach_handler(_RESOLVER_LOGGER)
    try:
        ref = RunArtifactRef(project_id="proj-1", output_path=str(target))
        m = resolver.materialize_run(ref)
    finally:
        target_logger.removeHandler(handler)

    assert m.path == target.resolve()
    # Nessun warning: None su TEMP_DIR/DATA_DIR e' skip legittimo
    trusted_warnings = [
        r for r in handler.records
        if r.levelno == logging.WARNING and "trusted_roots build" in r.getMessage()
    ]
    assert trusted_warnings == [], (
        f"atteso 0 warning su skip legittimo, ricevuto: "
        f"{[r.getMessage() for r in trusted_warnings]}"
    )


# BREADCRUMB su fallimento parziale (lista non vuota, warning emesso)


def test_trusted_roots_results_dir_resolve_fails_logs_warning(
    resolver, monkeypatch, tmp_path
):
    """`RESULTS_DIR` resolve esplode + TEMP_DIR valido → warning con
    exc_info=True + fallback continua (TEMP_DIR nel trusted_roots).
    Path dentro TEMP_DIR viene comunque accettato."""
    from app import config as _cfg

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    target = temp_dir / "out.csv"
    target.write_text("x", encoding="utf-8")

    # RESULTS_DIR non ha un .resolve() metodo perche' e' None → TypeError
    fake_settings = SimpleNamespace(
        RESULTS_DIR=None,
        TEMP_DIR=temp_dir,
        DATA_DIR=None,
    )
    monkeypatch.setattr(_cfg, "settings", fake_settings, raising=False)

    handler, target_logger = _attach_handler(_RESOLVER_LOGGER)
    try:
        ref = RunArtifactRef(project_id="proj-1", output_path=str(target))
        m = resolver.materialize_run(ref)
    finally:
        target_logger.removeHandler(handler)

    assert m.path == target.resolve()
    # NOTE: emesso per RESULTS_DIR, con exc_info presente
    results_warnings = [
        r for r in handler.records
        if r.levelno == logging.WARNING
        and "trusted_roots build" in r.getMessage()
        and "RESULTS_DIR" in r.getMessage()
    ]
    assert len(results_warnings) == 1
    # exc_info=True su un record diventa una tuple (exc_type, exc, tb)
    # popolata da Logger.findCaller; assert non-None per evitare
    # accoppiamento al tipo specifico.
    assert results_warnings[0].exc_info is not None


def test_trusted_roots_temp_dir_resolve_fails_logs_warning_and_continues(
    resolver, monkeypatch, tmp_path
):
    """RESULTS_DIR valido + TEMP_DIR settato a valore non-PathLike (int) →
    `Path(base)` solleva TypeError → warning con attr=TEMP_DIR + exc_info,
    trusted_roots contiene comunque RESULTS_DIR/proj, path legit accettato."""
    from app import config as _cfg

    results_dir = tmp_path / "results"
    (results_dir / "proj-1").mkdir(parents=True)
    target = results_dir / "proj-1" / "out.csv"
    target.write_text("x", encoding="utf-8")

    # TEMP_DIR = int (truthy ma non-PathLike) → `if not base` passa,
    # `Path(base)` solleva TypeError → caught da `except Exception as e`.
    # Questo riproduce il contratto del fix: qualsiasi Exception in
    # costruzione/resolve di TEMP_DIR viene catturata + warning emesso,
    # il resto del trusted_roots resta valido.
    fake_settings = SimpleNamespace(
        RESULTS_DIR=results_dir,
        TEMP_DIR=12345,  # int: truthy ma Path(12345) → TypeError
        DATA_DIR=None,
    )
    monkeypatch.setattr(_cfg, "settings", fake_settings, raising=False)

    handler, target_logger = _attach_handler(_RESOLVER_LOGGER)
    try:
        ref = RunArtifactRef(project_id="proj-1", output_path=str(target))
        m = resolver.materialize_run(ref)
    finally:
        target_logger.removeHandler(handler)

    # Path legit dentro RESULTS_DIR accettato (TEMP_DIR fail non blocca)
    assert m.path == target.resolve()
    temp_warnings = [
        r for r in handler.records
        if r.levelno == logging.WARNING
        and "trusted_roots build" in r.getMessage()
        and "attr=TEMP_DIR" in r.getMessage()
    ]
    assert len(temp_warnings) == 1
    assert temp_warnings[0].exc_info is not None


# FAIL-CLOSED — trusted_roots vuoti → raise ArtifactResolutionError


def test_trusted_roots_empty_raises_artifact_resolution_error(
    resolver, monkeypatch, tmp_path
):
    """Tutti e 3 i setting inutilizzabili: RESULTS_DIR=None (TypeError su
    Path(None)), TEMP_DIR=None (skip), DATA_DIR=None (skip) → trusted_roots=[]
    → raise ArtifactResolutionError canonico."""
    from app import config as _cfg

    fake_settings = SimpleNamespace(
        RESULTS_DIR=None,
        TEMP_DIR=None,
        DATA_DIR=None,
    )
    monkeypatch.setattr(_cfg, "settings", fake_settings, raising=False)

    ref = RunArtifactRef(project_id="proj-1", output_path=str(tmp_path / "any.csv"))
    with pytest.raises(ArtifactResolutionError, match="trusted_roots build fallita"):
        resolver.materialize_run(ref)


def test_trusted_roots_empty_error_message_mentions_config(
    resolver, monkeypatch, tmp_path
):
    """Messaggio eccezione contiene 'config' e i nomi delle 3 settings
    per diagnosticita' — il caller/admin deve sapere immediatamente
    che si tratta di config issue, non di path client."""
    from app import config as _cfg

    fake_settings = SimpleNamespace(
        RESULTS_DIR=None,
        TEMP_DIR=None,
        DATA_DIR=None,
    )
    monkeypatch.setattr(_cfg, "settings", fake_settings, raising=False)

    ref = RunArtifactRef(project_id="proj-1", output_path=str(tmp_path / "any.csv"))
    with pytest.raises(ArtifactResolutionError) as exc_info:
        resolver.materialize_run(ref)

    msg = str(exc_info.value)
    assert "config" in msg.lower()
    assert "RESULTS_DIR" in msg
    assert "DATA_DIR" in msg
    assert "TEMP_DIR" in msg
    # Distinto dal messaggio "fuori dai root consentiti" (errore diverso)
    assert "fuori dai root" not in msg
