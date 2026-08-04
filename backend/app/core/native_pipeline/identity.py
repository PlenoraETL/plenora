"""Materialisation and identity proof for pinned native executables."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Mapping

from app.core.native_pipeline.config import (
    IDENTITY_MODE_SHA256,
    IDENTITY_MODE_VERSION,
    NativeToolchainConfig,
    PinnedBinary,
)
from app.core.native_pipeline.errors import (
    ProcessSpawnFailed,
    StageStreamOverflow,
    StageTimeout,
    ToolchainIdentityError,
)
from app.core.native_pipeline.process import run_bounded_process
from app.core.native_pipeline.redaction import SecretRedactor
from app.core.native_pipeline.reports import decode_stream, strict_json_loads
from app.core.native_pipeline.stages import PipelineStage


_PROBE_STREAM_BYTES = 64 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_BINARY_STAGES: Mapping[str, PipelineStage] = MappingProxyType(
    {
        "database": PipelineStage.DATABASE_READ,
        "data": PipelineStage.DATA_PLAN,
        "io": PipelineStage.IO_CONVERT,
    }
)


def _identity_error(
    name: str, binary: PinnedBinary, reason: str, observed: str = ""
) -> ToolchainIdentityError:
    return ToolchainIdentityError(
        f"the pinned {name} binary did not prove its identity: {reason}",
        details={
            "binary": name,
            "reason": reason,
            "expected_identity": binary.identity,
            "expected_version": binary.version,
            "observed": observed,
        },
    )


def _validate_pin(name: str, binary: PinnedBinary) -> None:
    if len(binary.argv_prefix) != 1:
        reason = "argv_prefix_empty" if not binary.argv_prefix else "argv_prefix_not_single"
        raise _identity_error(name, binary, reason)
    executable = Path(binary.argv_prefix[0])
    if not executable.is_absolute():
        raise _identity_error(name, binary, "executable_not_absolute")
    try:
        metadata = executable.lstat()
    except OSError as exc:
        raise _identity_error(name, binary, "executable_lstat_failed", str(exc)) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise _identity_error(name, binary, "executable_not_regular")
    if binary.identity_mode not in {IDENTITY_MODE_VERSION, IDENTITY_MODE_SHA256}:
        raise _identity_error(name, binary, "identity_mode_invalid")
    if binary.identity_mode == IDENTITY_MODE_SHA256:
        if binary.identity_path is None:
            raise _identity_error(name, binary, "sha256_path_empty")
        if binary.identity_path != executable:
            raise _identity_error(name, binary, "sha256_path_mismatch")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", binary.expected_sha256):
            raise _identity_error(name, binary, "sha256_expected_invalid")


def materialize_toolchain(
    config: NativeToolchainConfig, *, workspace: Path
) -> NativeToolchainConfig:
    """Copy each executable into the private workspace before any probe."""
    originals = (
        ("database", config.database),
        ("data", config.data),
        ("io", config.io),
    )
    for name, binary in originals:
        _validate_pin(name, binary)
    snapshots: dict[str, PinnedBinary] = {}
    for name, binary in originals:
        destination = workspace / f"tool-{name}"
        _copy_executable(name, binary, destination, config.max_binary_bytes)
        snapshots[name] = replace(
            binary,
            argv_prefix=(str(destination),),
            identity_path=(
                destination if binary.identity_mode == IDENTITY_MODE_SHA256 else None
            ),
        )
    return replace(
        config,
        database=snapshots["database"],
        data=snapshots["data"],
        io=snapshots["io"],
    )


def _copy_executable(
    name: str, binary: PinnedBinary, destination: Path, limit: int
) -> None:
    source = Path(binary.argv_prefix[0])
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise _identity_error(name, binary, "snapshot_open_failed", str(exc)) from exc
    try:
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise _identity_error(name, binary, "snapshot_not_regular")
        if limit <= 0 or metadata.st_size > limit:
            raise _identity_error(name, binary, "snapshot_too_large")
        destination_fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o500
        )
        try:
            os.fchmod(destination_fd, 0o500)
            copied = 0
            while True:
                chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > limit:
                    raise _identity_error(name, binary, "snapshot_too_large")
                view = memoryview(chunk)
                while view:
                    view = view[os.write(destination_fd, view) :]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)


def verify_binary(
    name: str,
    binary: PinnedBinary,
    *,
    config: NativeToolchainConfig,
    env: Mapping[str, str],
    cwd: Path,
    redactor: SecretRedactor,
) -> str:
    """Prove the identity of a workspace snapshot."""
    _validate_pin(name, binary)
    if binary.identity_mode == IDENTITY_MODE_SHA256:
        return _verify_sha256(name, binary)
    try:
        outcome = run_bounded_process(
            (*binary.argv_prefix, "--version"),
            stage=_BINARY_STAGES[name],
            env=env,
            cwd=cwd,
            timeout_ms=config.identity_timeout_ms,
            max_stdout_bytes=_PROBE_STREAM_BYTES,
            max_stderr_bytes=_PROBE_STREAM_BYTES,
            redactor=redactor,
        )
    except (ProcessSpawnFailed, StageTimeout, StageStreamOverflow) as exc:
        raise _identity_error(name, binary, "probe_failed", str(exc)) from exc
    if outcome.exit_code != 0:
        raise _identity_error(name, binary, "version_exit", str(outcome.exit_code))
    line = _identity_line(outcome.stdout, outcome.stderr, redactor)
    if not line:
        raise _identity_error(name, binary, "empty_version_output")
    json_identity = _json_identity(line)
    if json_identity is not None:
        observed_identity, observed_version = json_identity
        if observed_identity != binary.identity:
            raise _identity_error(name, binary, "identity_mismatch", line)
        if observed_version != binary.version:
            raise _identity_error(name, binary, "version_mismatch", line)
        return binary.version
    tokens = line.split()
    if binary.identity not in tokens:
        raise _identity_error(name, binary, "identity_mismatch", line)
    if not any(token in {binary.version, f"v{binary.version}", f"V{binary.version}"} for token in tokens):
        raise _identity_error(name, binary, "version_mismatch", line)
    return binary.version


def _verify_sha256(name: str, binary: PinnedBinary) -> str:
    assert binary.identity_path is not None
    digest = hashlib.sha256()
    try:
        descriptor = os.open(
            binary.identity_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        with os.fdopen(descriptor, "rb") as handle:
            for chunk in iter(lambda: handle.read(_COPY_CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError as exc:
        raise _identity_error(name, binary, "sha256_read_failed", str(exc)) from exc
    observed = digest.hexdigest()
    if not hmac.compare_digest(observed, binary.expected_sha256.lower()):
        raise _identity_error(name, binary, "sha256_mismatch", observed)
    return binary.version


def _json_identity(line: str) -> tuple[str, str] | None:
    if not line.startswith("{"):
        return None
    try:
        document = strict_json_loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(document, dict) or document.get("status") != "ok":
        return None
    identity, version = document.get("identity"), document.get("version")
    if not isinstance(identity, str) or not isinstance(version, str):
        return None
    return identity, version


def _identity_line(stdout: bytes, stderr: bytes, redactor: SecretRedactor) -> str:
    for raw in (stdout, stderr):
        for candidate in decode_stream(raw, redactor).splitlines():
            if candidate.strip():
                return candidate.strip()
    return ""


def verify_toolchain(
    config: NativeToolchainConfig,
    *,
    env: Mapping[str, str],
    cwd: Path,
    redactor: SecretRedactor,
) -> Mapping[str, str]:
    for name, binary in (("database", config.database), ("data", config.data), ("io", config.io)):
        _validate_pin(name, binary)
    return MappingProxyType(
        {
            name: verify_binary(
                name, binary, config=config, env=env, cwd=cwd, redactor=redactor
            )
            for name, binary in (("database", config.database), ("data", config.data), ("io", config.io))
        }
    )


__all__ = ["materialize_toolchain", "verify_binary", "verify_toolchain"]
