"""Contract — predicato gate canvas-validation (Epic Canvas Validation §12).

``canvas_validation_gate_blocks(canvas_validated)`` è True solo se il flag
``CANVAS_VALIDATION_GATE_ENABLED`` è ON e il progetto NON è validato. È usato dagli
entry-point top-level (handler API /execute, scheduler), NON nei metodi di
esecuzione condivisi (che girano anche per sub-esecuzioni interne valide).
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _blocks(canvas_validated, *, flag, monkeypatch):
    from app.core.orchestrator import service as svc
    monkeypatch.setattr(
        svc.settings, "get",
        lambda k, d=None: flag if k == "CANVAS_VALIDATION_GATE_ENABLED" else d,
    )
    return svc.canvas_validation_gate_blocks(canvas_validated)


def test_gate_off_never_blocks(monkeypatch):
    assert _blocks(False, flag=False, monkeypatch=monkeypatch) is False
    assert _blocks(True, flag=False, monkeypatch=monkeypatch) is False


def test_gate_on_blocks_unvalidated_project(monkeypatch):
    assert _blocks(False, flag=True, monkeypatch=monkeypatch) is True


def test_gate_on_passes_validated_project(monkeypatch):
    assert _blocks(True, flag=True, monkeypatch=monkeypatch) is False
