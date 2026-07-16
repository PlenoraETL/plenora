"""
Snapshot test dello schema OpenAPI BE.

Salva/aggiorna `frontend/tests/fixtures/openapi.snapshot.json`, che
il contract test FE↔BE consuma per verificare che gli endpoint
chiamati dal FE (`http.get(...)`, `http.post(...)`) esistano BE-side.

Modalita`:
- Default (CI): fixture deve gia` esistere e il diff con lo schema
  generato deve essere vuoto. Fail se drift.
- ENV `UPDATE_OPENAPI_SNAPSHOT=1`: riscrive la fixture con lo schema
  corrente. Pattern d'uso quando si aggiunge/rimuove un endpoint:
  `UPDATE_OPENAPI_SNAPSHOT=1 pytest tests2/contract/test_openapi_snapshot.py`
  poi committare la fixture aggiornata.

La fixture e' in `frontend/tests/fixtures/` cosi` il test FE la legge
direttamente — niente cross-process IPC.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
_SNAPSHOT_PATH = _REPO_ROOT / "frontend" / "tests" / "fixtures" / "openapi.snapshot.json"


def _fetch_current_schema() -> dict:
    """Genera lo schema OpenAPI dell'app reale via TestClient."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/openapi.json")
    assert response.status_code == 200, f"GET /openapi.json failed: {response.status_code}"
    return response.json()


def _write_snapshot(schema: dict) -> None:
    _SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SNAPSHOT_PATH.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def test_openapi_snapshot_matches_or_update():
    """
    Verifica che `openapi.snapshot.json` matchi lo schema corrente.

    Modalita` UPDATE (env `UPDATE_OPENAPI_SNAPSHOT=1`): scrive la
    fixture invece di confrontare. Pattern espliciti per evitare
    auto-update silent.
    """
    current = _fetch_current_schema()

    if os.getenv("UPDATE_OPENAPI_SNAPSHOT") == "1":
        _write_snapshot(current)
        pytest.skip("snapshot aggiornato (UPDATE_OPENAPI_SNAPSHOT=1). Re-run senza la var per validare.")

    if not _SNAPSHOT_PATH.exists():
        _write_snapshot(current)
        pytest.skip(
            f"snapshot creato ex novo a {_SNAPSHOT_PATH.relative_to(_REPO_ROOT)} — "
            "committare e re-run."
        )

    expected = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    current_paths = set(current.get("paths", {}).keys())
    expected_paths = set(expected.get("paths", {}).keys())
    added = current_paths - expected_paths
    removed = expected_paths - current_paths
    assert not added and not removed, (
        f"OpenAPI paths drift vs snapshot:\n"
        f"  Aggiunti BE (non in snapshot): {sorted(added)}\n"
        f"  Rimossi BE (in snapshot ma non BE): {sorted(removed)}\n"
        f"Re-run con `UPDATE_OPENAPI_SNAPSHOT=1` se la modifica e' intenzionale."
    )

    # FastAPI/Pydantic may choose which of two same-named classes receives
    # the short component key according to module import order.  Compare the
    # logical schema titles (including multiplicity) so this contract remains
    # deterministic while still detecting added/removed models.
    cur_schemas = current.get("components", {}).get("schemas") or {}
    exp_schemas = expected.get("components", {}).get("schemas") or {}
    cur_models = Counter(schema.get("title") or key for key, schema in cur_schemas.items())
    exp_models = Counter(schema.get("title") or key for key, schema in exp_schemas.items())
    m_added = cur_models - exp_models
    m_removed = exp_models - cur_models
    assert not m_added and not m_removed, (
        f"OpenAPI models drift vs snapshot:\n"
        f"  Aggiunti: {sorted(m_added.elements())}\n"
        f"  Rimossi: {sorted(m_removed.elements())}\n"
        f"Re-run con `UPDATE_OPENAPI_SNAPSHOT=1` se intenzionale."
    )
