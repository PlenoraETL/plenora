"""
Contract test current IMDF contract — validate_publish_endpoint hydrata cache locale.

`/api/imdf/validate-publish` deve chiamare `_hydrate_local_cache` come fanno
"""
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Pre-import per evitare che monkeypatch di settings rompa init-time reads.
importlib.import_module("app.utils.logger")
imdf_router = importlib.import_module("app.core.imdf.router")

from app.shared.contracts.auth import CurrentUser

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]




@pytest.fixture
def superadmin() -> CurrentUser:
    """Bypassa ACL (_assert_bundle_group_access ritorna early su superadmin),
    isolando il test sulla logica hydrate."""
    return CurrentUser(user_id="u-sa", username="super", group_id="g-sa", role="superadmin")


@pytest.fixture
def fake_settings(tmp_path, monkeypatch):
    """Patch IMDF_BUNDLES_DIR sia su app.config che su imdf_router (gia'
    importato → ha riferimento proprio a settings)."""
    root = tmp_path / "imdf_bundles"
    root.mkdir()
    import app.config as config_mod
    settings = SimpleNamespace(IMDF_BUNDLES_DIR=root, IMDF_BUNDLE_TTL_HOURS=24)
    monkeypatch.setattr(config_mod, "settings", settings)
    monkeypatch.setattr(imdf_router, "settings", settings)
    return root


@pytest.fixture
def fake_validator(monkeypatch, fake_settings):
    """Stub di app.core.imdf.validator.validate_publish.

    Simula il check exists() del validator reale (validator.py:190): se la
    bundle dir non esiste sotto IMDF_BUNDLES_DIR, ritorna report con
    BUNDLE_MISSING. Altrimenti report base ok=True. Permette di verificare
    in modo isolato l'effetto di hydrate sul check exists() downstream.
    """
    # `imdf_router` fa `from app.core.imdf.validator import validate_publish`,
    # quindi il binding va patchato sul consumer.
    def _fake(bundle_task_id, **kwargs):
        bundle_dir = fake_settings / bundle_task_id
        report = {
            "ok": True,
            "summary": {"errors": 0, "warnings": 0},
            "top_issues": [],
        }
        if not bundle_dir.exists() or not bundle_dir.is_dir():
            report["ok"] = False
            report["summary"]["errors"] = 1
            report["top_issues"].append({
                "severity": "error",
                "code": "BUNDLE_MISSING",
                "message": f"Bundle '{bundle_task_id}' non trovato",
            })
        return report

    monkeypatch.setattr(imdf_router, "validate_publish", _fake)
    return _fake


def _make_request(bundle_task_id: str = "task-test"):
    return imdf_router.ImdfValidateRequest(
        bundle_task_id=bundle_task_id,
        connection_id="conn-1",
        fs_name="MyFS",
        mode="replace",
    )




def test_validate_publish_cached_locally_returns_report(
    fake_settings, fake_validator, superadmin, monkeypatch
):
    """Cache hit: bundle gia' presente localmente. Hydrate viene comunque
    chiamato (idempotenza), no-op nel caso reale (cache hit). Validator
    vede la dir e ritorna report base, no BUNDLE_MISSING."""
    bundle_dir = fake_settings / "task-test"
    bundle_dir.mkdir()
    (bundle_dir / "plenora_metadata.json").write_text(
        json.dumps({"project_id": "proj-1", "site_id": "S1"}),
        encoding="utf-8",
    )

    import app.core.imdf.storage as storage_mod
    mock_ensure = MagicMock(return_value=bundle_dir)
    monkeypatch.setattr(storage_mod, "ensure_local_bundle", mock_ensure)

    report = imdf_router.validate_publish_endpoint(_make_request(), current_user=superadmin)

    assert mock_ensure.called, "hydrate deve essere chiamato anche su cache hit (idempotenza)"
    assert mock_ensure.call_args.args[0] == "task-test"
    assert report["ok"] is True
    codes = [issue["code"] for issue in report["top_issues"]]
    assert "BUNDLE_MISSING" not in codes




def test_validate_publish_hydrates_from_s3_when_cache_miss(
    fake_settings, fake_validator, superadmin, monkeypatch
):
    """Cache miss: bundle solo-S3, hydrate scarica e popola la cache,
    validator vede dir popolata, report valido (NON BUNDLE_MISSING).

    Previous behavior (current IMDF contract): hydrate non era chiamato → exists()=False →
    validator restituiva BUNDLE_MISSING falso-positivo.
    """
    bundle_dir = fake_settings / "task-test"
    assert not bundle_dir.exists(), "precondizione: bundle non locale"

    def _fake_ensure(task_id):
        local = fake_settings / task_id
        local.mkdir(exist_ok=True)
        (local / "plenora_metadata.json").write_text(
            json.dumps({"project_id": "proj-1", "site_id": "S1"}),
            encoding="utf-8",
        )
        return local

    import app.core.imdf.storage as storage_mod
    monkeypatch.setattr(storage_mod, "ensure_local_bundle", _fake_ensure)

    report = imdf_router.validate_publish_endpoint(_make_request(), current_user=superadmin)

    assert bundle_dir.exists(), "hydrate deve aver popolato la cache"
    assert report["ok"] is True
    codes = [issue["code"] for issue in report["top_issues"]]
    assert "BUNDLE_MISSING" not in codes




def test_validate_publish_returns_bundle_missing_when_s3_empty(
    fake_settings, fake_validator, superadmin, monkeypatch
):
    """Bundle assente ovunque: hydrate solleva FileNotFoundError, _hydrate_
    local_cache silent pass (router.py:72-74), exists() resta False, validator
    restituisce BUNDLE_MISSING. R5: nessun HYDRATE_FAILED introdotto."""
    import app.core.imdf.storage as storage_mod

    def _fake_ensure(task_id):
        raise FileNotFoundError(f"bundle {task_id} assente su S3")

    monkeypatch.setattr(storage_mod, "ensure_local_bundle", _fake_ensure)

    report = imdf_router.validate_publish_endpoint(_make_request(), current_user=superadmin)

    assert report["ok"] is False
    codes = [issue["code"] for issue in report["top_issues"]]
    assert "BUNDLE_MISSING" in codes
    assert "HYDRATE_FAILED" not in codes, "R5: error code unico per non-disponibilita'"




def test_validate_publish_keeps_bundle_missing_on_hydrate_exception(
    fake_settings, fake_validator, superadmin, monkeypatch
):
    """Hydrate solleva exception non-FileNotFoundError (network 5xx, S3
    timeout, credenziali rifiutate, ...). _hydrate_local_cache logga
    warning + silent pass (router.py:75-76). exists() resta False ->
    validator restituisce BUNDLE_MISSING. R5 enforcement."""
    import app.core.imdf.storage as storage_mod

    def _fake_ensure(task_id):
        raise ConnectionError("S3 endpoint timeout")

    monkeypatch.setattr(storage_mod, "ensure_local_bundle", _fake_ensure)

    report = imdf_router.validate_publish_endpoint(_make_request(), current_user=superadmin)

    assert report["ok"] is False
    codes = [issue["code"] for issue in report["top_issues"]]
    assert "BUNDLE_MISSING" in codes
    assert "HYDRATE_FAILED" not in codes
