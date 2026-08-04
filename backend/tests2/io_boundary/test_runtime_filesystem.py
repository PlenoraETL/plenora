from __future__ import annotations

from pathlib import Path
import shutil

from app.core.io.runtime_filesystem import make_runtime_temp_dir


def test_make_runtime_temp_dir_honors_declared_parent(tmp_path: Path) -> None:
    parent = tmp_path / "runtime-root"
    parent.mkdir()

    created = Path(
        make_runtime_temp_dir(prefix="plenora-native-", parent=parent)
    )
    try:
        assert created.parent == parent
        assert created.name.startswith("plenora-native-")
    finally:
        shutil.rmtree(created)
