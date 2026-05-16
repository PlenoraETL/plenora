"""Contract tests for centralized path-only temp adapters."""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_path_only_adapter_materializes_under_settings_temp_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.config import settings
    from app.core.results.temp_paths import (
        PATH_ONLY_ADAPTER_PREFIX,
        cleanup_path_only_adapter,
        write_path_only_adapter_bytes,
    )

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    temp_root = Path(settings.TEMP_DIR)

    path = write_path_only_adapter_bytes(b"payload", suffix=".csv")
    try:
        assert path.exists()
        assert path.read_bytes() == b"payload"
        assert path.name.startswith(PATH_ONLY_ADAPTER_PREFIX)
        assert path.suffix == ".csv"
        assert path.resolve().is_relative_to(temp_root.resolve())
    finally:
        cleanup_path_only_adapter(path)


def test_path_only_adapter_cleanup_is_prefix_and_root_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.config import settings
    from app.core.results.temp_paths import (
        PATH_ONLY_ADAPTER_PREFIX,
        cleanup_path_only_adapter,
        write_path_only_adapter_bytes,
    )

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    temp_root = Path(settings.TEMP_DIR)

    owned = write_path_only_adapter_bytes(b"x", suffix="txt")
    assert cleanup_path_only_adapter(owned) is True
    assert cleanup_path_only_adapter(owned) is False

    wrong_prefix = temp_root / "not-owned.txt"
    wrong_prefix.write_bytes(b"keep")
    try:
        assert cleanup_path_only_adapter(wrong_prefix) is False
        assert wrong_prefix.exists()
    finally:
        wrong_prefix.unlink(missing_ok=True)

    outside = tmp_path / f"{PATH_ONLY_ADAPTER_PREFIX}outside.txt"
    outside.write_bytes(b"keep")
    try:
        assert cleanup_path_only_adapter(outside) is False
        assert outside.exists()
    finally:
        outside.unlink(missing_ok=True)


def test_result_temp_paths_are_adapter_paths_under_settings_temp_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.config import settings
    from app.core.results.temp_paths import (
        RESULT_CACHE_TEMP_PREFIX,
        RESULT_STREAM_TEMP_PREFIX,
        cleanup_path_only_adapter,
        get_temp_output_path,
        make_cached_result_path,
    )

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    temp_root = Path(settings.TEMP_DIR)

    stream_path = get_temp_output_path("out").with_suffix(".csv")
    cached_path = make_cached_result_path(".json")

    try:
        assert stream_path.parent.name.startswith(RESULT_STREAM_TEMP_PREFIX)
        assert stream_path.resolve().is_relative_to(temp_root.resolve())
        assert cached_path.name.startswith(RESULT_CACHE_TEMP_PREFIX)
        assert cached_path.resolve().is_relative_to(temp_root.resolve())
    finally:
        cleanup_path_only_adapter(
            stream_path.parent,
            prefix=RESULT_STREAM_TEMP_PREFIX,
        )
