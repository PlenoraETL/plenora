"""Contract — gate a esecuzione (Epic Canvas Validation §12), dietro feature-flag.

_canvas_validation_gate blocca il run (PipelineArtifact failure) se il flag è ON
e il progetto NON è validato (stato persistito ``canvas_validated``, coerente col
gate scheduler). None altrimenti. Flag OFF → mai blocco.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _gate(canvas_validated, *, flag, monkeypatch):
    from app.core.orchestrator import service as svc
    monkeypatch.setattr(svc.settings, "get", lambda k, d=None: flag if k == "CANVAS_VALIDATION_GATE_ENABLED" else d)
    return svc._canvas_validation_gate("p", canvas_validated)


def test_gate_off_never_blocks(monkeypatch):
    # flag OFF → nessun blocco anche se non validato
    assert _gate(False, flag=False, monkeypatch=monkeypatch) is None


def test_gate_on_blocks_unvalidated_project(monkeypatch):
    result = _gate(False, flag=True, monkeypatch=monkeypatch)
    assert result is not None
    assert result["success"] is False


def test_gate_on_passes_validated_project(monkeypatch):
    assert _gate(True, flag=True, monkeypatch=monkeypatch) is None
