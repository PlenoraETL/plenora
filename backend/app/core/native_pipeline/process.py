"""Bounded, shell-free subprocess runner for the pinned binaries.

Invariants enforced here, per spawn:

* argv is a list and ``shell`` is false — no word splitting, no interpolation;
* the child environment is exactly what the caller hands over (allowlisted);
* stdin is ``/dev/null`` — a stage can never block waiting for input;
* stdout and stderr are drained on dedicated threads with a hard byte bound,
  so a chatty child cannot exhaust backend memory nor deadlock on a full pipe;
* a stage that exceeds its wall-clock budget or a stream bound is killed, and
  the failure is typed rather than reported as a normal exit.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import stat
import subprocess
import threading
import time
from typing import IO, Mapping, Sequence

from app.core.native_pipeline.errors import (
    ProcessSpawnFailed,
    StageArtifactOverflow,
    StageStreamOverflow,
    StageTimeout,
)
from app.core.native_pipeline.redaction import SecretRedactor
from app.core.native_pipeline.stages import PipelineStage


_READ_CHUNK_BYTES = 64 * 1024
_POLL_INTERVAL_SECONDS = 0.01
_REAP_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """What a completed child process produced, within bounds."""

    argv: tuple[str, ...]
    exit_code: int
    stdout: bytes
    stderr: bytes
    stdout_bytes: int
    stderr_bytes: int
    duration_ms: int


class _BoundedStreamReader(threading.Thread):
    """Drains a pipe, keeping at most ``limit_bytes`` of it.

    Draining continues past the limit so the child cannot wedge on a full
    pipe; the surplus is counted and discarded, and the caller turns the
    overflow flag into a typed failure.
    """

    def __init__(self, stream: IO[bytes], limit_bytes: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit_bytes = limit_bytes
        self._chunks: list[bytes] = []
        self.total_bytes = 0
        self.overflowed = False
        self.failure: BaseException | None = None

    def run(self) -> None:
        kept = 0
        try:
            while True:
                chunk = self._stream.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                self.total_bytes += len(chunk)
                if kept + len(chunk) <= self._limit_bytes:
                    self._chunks.append(chunk)
                    kept += len(chunk)
                else:
                    self.overflowed = True
        except BaseException as exc:
            self.failure = exc

    @property
    def data(self) -> bytes:
        return b"".join(self._chunks)


def _terminate(process: "subprocess.Popen[bytes]", process_group: int) -> None:
    """Kill a child and, on POSIX, the session it leads.

    Both suppressions are secondary cleanup: the process may have exited
    between the poll and the signal, which is not an error for the caller.
    """
    if hasattr(os, "killpg"):
        with suppress(OSError):
            # The pgid is captured as pid at spawn. Never rediscover it through
            # an exited leader: its descendants can still own pipes and live.
            os.killpg(process_group, signal.SIGKILL)
    with suppress(OSError):
        process.kill()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_REAP_TIMEOUT_SECONDS)


def _spawn(
    command: list[str], *, env: Mapping[str, str], cwd: Path
) -> "subprocess.Popen[bytes]":
    keywords: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": dict(env),
        "cwd": str(cwd),
        "shell": False,
        "close_fds": True,
    }
    # Own session: a kill reaches the whole stage, not just its leader.
    keywords["start_new_session"] = True
    return subprocess.Popen(command, **keywords)  # type: ignore[arg-type]


def run_bounded_process(
    argv: Sequence[str],
    *,
    stage: PipelineStage,
    env: Mapping[str, str],
    cwd: Path,
    timeout_ms: int,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
    redactor: SecretRedactor,
    watched_output_path: Path | None = None,
    max_output_bytes: int | None = None,
) -> ProcessOutcome:
    """Run one pinned binary under the invariants documented above."""
    if os.name != "posix" or not hasattr(os, "killpg"):
        raise ProcessSpawnFailed(stage, "the native runner requires POSIX process groups")
    command = [str(part) for part in argv]
    redacted_argv = redactor.redact_argv(command)

    started = time.monotonic()
    try:
        process = _spawn(command, env=env, cwd=cwd)
    except (OSError, ValueError) as exc:
        raise ProcessSpawnFailed(
            stage, f"cannot start {redacted_argv[0] if redacted_argv else '<empty>'}: {exc}"
        ) from exc

    stdout_pipe, stderr_pipe = process.stdout, process.stderr
    process_group = process.pid
    if stdout_pipe is None or stderr_pipe is None:
        # Both streams are requested as pipes above; an absent one means the
        # child is unusable, so it is killed rather than left half-read.
        _terminate(process, process_group)
        raise ProcessSpawnFailed(stage, "the child exposed no output pipes")

    stdout_reader = _BoundedStreamReader(stdout_pipe, max_stdout_bytes)
    stderr_reader = _BoundedStreamReader(stderr_pipe, max_stderr_bytes)
    stdout_reader.start()
    stderr_reader.start()

    deadline = started + timeout_ms / 1000.0
    timed_out = False
    artifact_overflowed = False
    try:
        while True:
            if watched_output_path is not None and max_output_bytes is not None:
                try:
                    output_metadata = watched_output_path.lstat()
                    artifact_overflowed = stat.S_ISREG(output_metadata.st_mode) and (
                        output_metadata.st_size > max_output_bytes
                    )
                except FileNotFoundError:
                    artifact_overflowed = False
                except OSError:
                    # Shape/access failures are classified by the existing
                    # nofollow post-stage validation unless another bound fires.
                    artifact_overflowed = False
                if artifact_overflowed:
                    break
            if process.poll() is not None:
                break
            if stdout_reader.failure is not None or stderr_reader.failure is not None:
                break
            if stdout_reader.overflowed or stderr_reader.overflowed:
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(_POLL_INTERVAL_SECONDS)
    finally:
        # Always terminate the known group, including when its leader already
        # exited successfully while a descendant retained a pipe.
        _terminate(process, process_group)
        join_deadline = time.monotonic() + _REAP_TIMEOUT_SECONDS
        stdout_reader.join(timeout=max(0.0, join_deadline - time.monotonic()))
        stderr_reader.join(timeout=max(0.0, join_deadline - time.monotonic()))
        readers_stuck = stdout_reader.is_alive() or stderr_reader.is_alive()
        # BufferedReader.close can wait on a reader-thread lock. Only close a
        # pipe after its reader has conclusively stopped.
        if not stdout_reader.is_alive():
            with suppress(OSError):
                stdout_pipe.close()
        if not stderr_reader.is_alive():
            with suppress(OSError):
                stderr_pipe.close()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_REAP_TIMEOUT_SECONDS)

    duration_ms = int((time.monotonic() - started) * 1000)
    if timed_out:
        raise StageTimeout(stage, timeout_ms)
    if artifact_overflowed:
        raise StageArtifactOverflow(stage, max_output_bytes or 0)
    if stdout_reader.overflowed:
        raise StageStreamOverflow(stage, "stdout", max_stdout_bytes)
    if stderr_reader.overflowed:
        raise StageStreamOverflow(stage, "stderr", max_stderr_bytes)
    if readers_stuck:
        raise ProcessSpawnFailed(stage, "child output pipes did not close after group termination")
    if stdout_reader.failure is not None or stderr_reader.failure is not None:
        raise ProcessSpawnFailed(stage, "child output pipe reader failed")

    return ProcessOutcome(
        argv=redacted_argv,
        exit_code=process.returncode if process.returncode is not None else -1,
        stdout=stdout_reader.data,
        stderr=stderr_reader.data,
        stdout_bytes=stdout_reader.total_bytes,
        stderr_bytes=stderr_reader.total_bytes,
        duration_ms=duration_ms,
    )


__all__ = ["ProcessOutcome", "run_bounded_process"]
