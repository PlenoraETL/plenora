"""Per-run isolated workspace and child environment.

Every stage writes into a private directory that exists for the duration of one
run. The child processes also get that directory as their temp root, so a tool
that spills scratch files cannot land them in the backend's shared temp space.
The workspace is removed on every exit path, success or failure.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import Iterator, Mapping

from app.config import settings
from app.core.io.runtime_filesystem import make_runtime_temp_dir
from app.core.native_pipeline.config import NativeToolchainConfig
from app.core.native_pipeline.errors import WorkspaceCleanupFailed


_WORKSPACE_PREFIX = "plenora-native-"


@dataclass(frozen=True, slots=True)
class Workspace:
    """Private directories of a single run."""

    root: Path
    scratch: Path

    def stage_path(self, filename: str) -> Path:
        return self.root / filename


@contextmanager
def open_workspace(config: NativeToolchainConfig) -> Iterator[Workspace]:
    """Create the run workspace and remove it unconditionally on exit."""
    parent = config.workspace_root
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    root = Path(
        make_runtime_temp_dir(prefix=_WORKSPACE_PREFIX, parent=parent)
    )
    scratch = root / "scratch"
    scratch.mkdir()
    completed = False
    try:
        yield Workspace(root=root, scratch=scratch)
        completed = True
    finally:
        try:
            shutil.rmtree(root)
        except OSError as exc:
            if completed:
                raise WorkspaceCleanupFailed(
                    f"cannot remove native workspace {root}: {exc}",
                    details={"workspace": str(root)},
                ) from exc
            # Secondary cleanup must not hide the primary stage failure.
            pass


def build_child_env(
    config: NativeToolchainConfig,
    *,
    scratch: Path,
    secret_env_var: str | None = None,
    secret_value: str | None = None,
) -> Mapping[str, str]:
    """Build the allowlisted child environment for one spawn.

    Only the database stage passes ``secret_env_var``: every other child, the
    identity probes included, runs without the DSN in its environment.
    """
    env: dict[str, str] = {}
    for name in config.env_allowlist:
        value = settings.get_raw(name)
        if value is not None:
            env[name] = str(value)

    scratch_path = str(scratch)
    env["TMPDIR"] = scratch_path
    env["TEMP"] = scratch_path
    env["TMP"] = scratch_path

    if secret_env_var is not None and secret_value is not None:
        env[secret_env_var] = secret_value
    return MappingProxyType(env)


__all__ = ["Workspace", "build_child_env", "open_workspace"]
