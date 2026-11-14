"""Regression checks for false positives in the offline version gate."""

from pathlib import Path
import runpy
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = runpy.run_path(str(ROOT / "scripts/check_python_version.py"))


def test_runtime_rejects_wrong_minor():
    with patch("sys.version_info", (3, 0, 0)):
        with pytest.raises(ValueError, match="Expected CPython"):
            CONTRACT["check_runtime"](CONTRACT["read_version"](ROOT / ".python-version"))


def test_runtime_rejects_free_threaded_build():
    with patch("sysconfig.get_config_var", return_value=1):
        import sys
        with pytest.raises(ValueError, match="free-threaded"):
            CONTRACT["check_runtime"](".".join(map(str, sys.version_info[:2])))


@pytest.mark.parametrize("mutation", ["comment", "wrong-worker"])
def test_configuration_rejects_comment_markers_and_image_drift(mutation):
    original = CONTRACT["active_text"]

    def mutated(path):
        text = original(path)
        if mutation == "comment" and path == ROOT / "backend/Dockerfile":
            # A commented installation must not satisfy the gate.
            return text.replace('"python=$(cat /opt/plenora/.python-version)"', "")
        if mutation == "wrong-worker" and path == ROOT / "docker-compose.yml":
            version = CONTRACT["read_version"](ROOT / ".python-version")
            return text.replace("plenora-backend:py" + version.replace(".", ""),
                                "plenora-backend:obsolete", 1)
        return text

    with patch.dict(CONTRACT["check_configuration"].__globals__, active_text=mutated):
        with pytest.raises(ValueError):
            CONTRACT["check_configuration"](ROOT)
