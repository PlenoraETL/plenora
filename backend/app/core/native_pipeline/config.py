"""Pinned toolchain configuration.

Binaries are configured as JSON arrays containing exactly one absolute direct
executable path, never a wrapper, prefix argument, shell string, or PATH
lookup. An unconfigured binary is fail-closed, so the default configuration
points nowhere and the native path ships disabled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from app.config import settings


EXPECTED_DATABASE_VERSION = "1.1.0"
EXPECTED_DATA_VERSION = "1.0.2"
EXPECTED_IO_VERSION = "1.0.1"

DATABASE_IDENTITY = "plenora-database"
DATA_IDENTITY = "plenora-data-tools"
IO_IDENTITY = "plenora-io"

# Everything a pinned binary is allowed to inherit from the backend process.
# TMPDIR/TEMP/TMP are deliberately absent: the runner overrides them with the
# per-run isolated scratch directory.
DEFAULT_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "LD_LIBRARY_PATH",
)


IDENTITY_MODE_VERSION = "version"
IDENTITY_MODE_SHA256 = "sha256"


@dataclass(frozen=True, slots=True)
class PinnedBinary:
    """One native binary pinned to a proven identity and version."""

    argv_prefix: tuple[str, ...]
    identity: str
    version: str
    identity_mode: str = IDENTITY_MODE_VERSION
    identity_path: Path | None = None
    expected_sha256: str = ""


def _argv_prefix(key: str) -> tuple[str, ...]:
    raw = str(settings.get(key, "") or "").strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{key} must be a JSON array of argv entries, for example "
            '["/opt/plenora/bin/plenora-database"]'
        ) from exc
    if not isinstance(parsed, list) or len(parsed) != 1:
        raise ValueError(f"{key} must contain exactly one executable path")
    if not all(isinstance(part, str) and part for part in parsed):
        raise ValueError(f"{key} entries must all be non-empty strings")
    if not Path(parsed[0]).is_absolute():
        raise ValueError(f"{key} executable path must be absolute")
    return tuple(parsed)


def _int_setting(key: str, default: int) -> int:
    value = settings.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer number of units") from exc


def _workspace_root() -> Path | None:
    raw = str(settings.get("NATIVE_PIPELINE_WORKSPACE_DIR", "") or "").strip()
    return Path(raw) if raw else None


def _identity_mode(key: str, default: str) -> str:
    value = str(settings.get(key, default) or "").strip().lower()
    if value not in {IDENTITY_MODE_VERSION, IDENTITY_MODE_SHA256}:
        raise ValueError(f"{key} must be 'version' or 'sha256'")
    return value


def _optional_path(key: str) -> Path | None:
    raw = str(settings.get(key, "") or "").strip()
    return Path(raw) if raw else None


@dataclass(frozen=True, slots=True)
class NativeToolchainConfig:
    """Immutable configuration of one native run."""

    enabled: bool
    database: PinnedBinary
    data: PinnedBinary
    io: PinnedBinary
    stage_timeout_ms: int = 300_000
    identity_timeout_ms: int = 10_000
    max_stdout_bytes: int = 1_048_576
    max_stderr_bytes: int = 262_144
    max_report_bytes: int = 262_144
    max_binary_bytes: int = 512 * 1024 * 1024
    max_stage_artifact_bytes: int = 512 * 1024 * 1024
    max_plan_bytes: int = 16 * 1024 * 1024
    workspace_root: Path | None = None
    env_allowlist: tuple[str, ...] = field(default=DEFAULT_ENV_ALLOWLIST)

    @classmethod
    def from_settings(cls) -> "NativeToolchainConfig":
        """Build the configuration from environment-backed settings.

        Raises ``ValueError`` on a malformed pinning: a half-declared toolchain
        must never silently degrade into "run whatever is on PATH".
        """
        return cls(
            enabled=bool(settings.get("NATIVE_PIPELINE_ENABLED", False)),
            database=PinnedBinary(
                argv_prefix=_argv_prefix("NATIVE_PIPELINE_DATABASE_ARGV"),
                identity=str(
                    settings.get("NATIVE_PIPELINE_DATABASE_IDENTITY", DATABASE_IDENTITY)
                ),
                version=str(
                    settings.get(
                        "NATIVE_PIPELINE_DATABASE_VERSION", EXPECTED_DATABASE_VERSION
                    )
                ),
                identity_mode=_identity_mode(
                    "NATIVE_PIPELINE_DATABASE_IDENTITY_MODE", IDENTITY_MODE_SHA256
                ),
                identity_path=_optional_path("NATIVE_PIPELINE_DATABASE_IDENTITY_PATH"),
                expected_sha256=str(
                    settings.get("NATIVE_PIPELINE_DATABASE_SHA256", "") or ""
                ).strip().lower(),
            ),
            data=PinnedBinary(
                argv_prefix=_argv_prefix("NATIVE_PIPELINE_DATA_ARGV"),
                identity=str(
                    settings.get("NATIVE_PIPELINE_DATA_IDENTITY", DATA_IDENTITY)
                ),
                version=str(
                    settings.get("NATIVE_PIPELINE_DATA_VERSION", EXPECTED_DATA_VERSION)
                ),
                identity_mode=_identity_mode(
                    "NATIVE_PIPELINE_DATA_IDENTITY_MODE", IDENTITY_MODE_SHA256
                ),
                identity_path=_optional_path("NATIVE_PIPELINE_DATA_IDENTITY_PATH"),
                expected_sha256=str(
                    settings.get("NATIVE_PIPELINE_DATA_SHA256", "") or ""
                ).strip().lower(),
            ),
            io=PinnedBinary(
                argv_prefix=_argv_prefix("NATIVE_PIPELINE_IO_ARGV"),
                identity=str(settings.get("NATIVE_PIPELINE_IO_IDENTITY", IO_IDENTITY)),
                version=str(
                    settings.get("NATIVE_PIPELINE_IO_VERSION", EXPECTED_IO_VERSION)
                ),
                identity_mode=_identity_mode(
                    "NATIVE_PIPELINE_IO_IDENTITY_MODE", IDENTITY_MODE_SHA256
                ),
                identity_path=_optional_path("NATIVE_PIPELINE_IO_IDENTITY_PATH"),
                expected_sha256=str(
                    settings.get("NATIVE_PIPELINE_IO_SHA256", "") or ""
                ).strip().lower(),
            ),
            stage_timeout_ms=_int_setting("NATIVE_PIPELINE_STAGE_TIMEOUT_MS", 300_000),
            identity_timeout_ms=_int_setting(
                "NATIVE_PIPELINE_IDENTITY_TIMEOUT_MS", 10_000
            ),
            max_stdout_bytes=_int_setting("NATIVE_PIPELINE_MAX_STDOUT_BYTES", 1_048_576),
            max_stderr_bytes=_int_setting("NATIVE_PIPELINE_MAX_STDERR_BYTES", 262_144),
            max_report_bytes=_int_setting("NATIVE_PIPELINE_MAX_REPORT_BYTES", 262_144),
            max_binary_bytes=_int_setting(
                "NATIVE_PIPELINE_MAX_BINARY_BYTES", 512 * 1024 * 1024
            ),
            max_stage_artifact_bytes=_int_setting(
                "NATIVE_PIPELINE_MAX_STAGE_ARTIFACT_BYTES", 512 * 1024 * 1024
            ),
            max_plan_bytes=_int_setting(
                "NATIVE_PIPELINE_MAX_PLAN_BYTES", 16 * 1024 * 1024
            ),
            workspace_root=_workspace_root(),
        )


__all__ = [
    "DATABASE_IDENTITY",
    "DATA_IDENTITY",
    "DEFAULT_ENV_ALLOWLIST",
    "EXPECTED_DATABASE_VERSION",
    "EXPECTED_DATA_VERSION",
    "EXPECTED_IO_VERSION",
    "IDENTITY_MODE_SHA256",
    "IDENTITY_MODE_VERSION",
    "IO_IDENTITY",
    "NativeToolchainConfig",
    "PinnedBinary",
]
