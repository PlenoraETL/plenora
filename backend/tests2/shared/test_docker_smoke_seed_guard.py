"""Guards for deterministic Docker API smoke credential seeding."""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SEED = _REPO_ROOT / "backend" / "app" / "core" / "auth" / "smoke_seed.py"


def test_docker_smoke_seed_is_explicit_and_non_prod_guarded():
    text = _SEED.read_text(encoding="utf-8")
    for marker in (
        "PLENORA_SMOKE_USERNAME",
        "PLENORA_SMOKE_PASSWORD",
        "Refusing to seed smoke credentials in production/staging",
        "db.create_group",
        "db.create_user",
        "db.update_user_password",
    ):
        assert marker in text
