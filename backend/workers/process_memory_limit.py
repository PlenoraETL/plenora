"""Best available hard process-memory limit for each operating system."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.io.runtime_filesystem import write_kernel_control


class ProcessMemoryLimitHandle(Protocol):
    backend: str
    enforced: bool
    detail: str

    def close(self) -> None: ...


@dataclass
class FallbackMemoryLimitHandle:
    backend: str = "psutil-supervisor"
    enforced: bool = False
    detail: str = "hard OS limit unavailable; RSS supervisor active"

    def close(self) -> None:
        return None


class _WindowsJobMemoryLimitHandle:
    backend = "windows-job-object"
    enforced = True

    def __init__(self, job_handle: int, *, limit_bytes: int) -> None:
        self._job_handle = job_handle
        self.detail = f"process_memory_limit={limit_bytes}"

    def close(self) -> None:
        if not self._job_handle:
            return
        ctypes.windll.kernel32.CloseHandle(self._job_handle)
        self._job_handle = 0


class _LinuxCgroupMemoryLimitHandle:
    backend = "linux-cgroup-v2"
    enforced = True

    def __init__(self, path: Path, *, limit_bytes: int) -> None:
        self._path = path
        self.detail = f"memory.max={limit_bytes} path={path.name}"

    def close(self) -> None:
        try:
            self._path.rmdir()
        except OSError:
            pass


def _apply_windows_job_limit(
    pid: int,
    limit_bytes: int,
) -> ProcessMemoryLimitHandle:
    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    process_handle = None
    try:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00000100 | 0x00002000
        info.ProcessMemoryLimit = int(limit_bytes)
        if not kernel32.SetInformationJobObject(
            job,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise OSError(
                ctypes.get_last_error(),
                "SetInformationJobObject failed",
            )
        process_handle = kernel32.OpenProcess(
            0x0100 | 0x0001 | 0x1000,
            False,
            int(pid),
        )
        if not process_handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        if not kernel32.AssignProcessToJobObject(job, process_handle):
            raise OSError(
                ctypes.get_last_error(),
                "AssignProcessToJobObject failed",
            )
        return _WindowsJobMemoryLimitHandle(
            int(job),
            limit_bytes=limit_bytes,
        )
    except (OSError, ValueError, ctypes.ArgumentError):
        kernel32.CloseHandle(job)
        raise
    finally:
        if process_handle:
            kernel32.CloseHandle(process_handle)


def _current_cgroup_v2_path() -> Path | None:
    try:
        for line in Path("/proc/self/cgroup").read_text(
            encoding="utf-8"
        ).splitlines():
            if line.startswith("0::"):
                relative = line.partition("::")[2].lstrip("/")
                return Path("/sys/fs/cgroup") / relative
    except OSError:
        return None
    return None


def _apply_linux_cgroup_limit(
    pid: int,
    limit_bytes: int,
) -> ProcessMemoryLimitHandle:
    parent = _current_cgroup_v2_path()
    if parent is None or not (parent / "cgroup.controllers").exists():
        raise OSError("cgroup v2 unavailable")
    child = parent / f"plenora-task-{os.getpid()}-{int(pid)}"
    child.mkdir(mode=0o700)
    try:
        write_kernel_control(child / "memory.max", limit_bytes)
        write_kernel_control(child / "cgroup.procs", pid)
    except OSError:
        try:
            child.rmdir()
        except OSError:
            pass
        raise
    return _LinuxCgroupMemoryLimitHandle(child, limit_bytes=limit_bytes)


def apply_process_memory_limit(
    *,
    pid: int,
    limit_bytes: int,
) -> ProcessMemoryLimitHandle:
    """Apply a hard limit where supported, otherwise return safe fallback."""
    if pid <= 0 or limit_bytes <= 0:
        return FallbackMemoryLimitHandle(detail="hard limit disabled")
    try:
        if os.name == "nt":
            return _apply_windows_job_limit(pid, limit_bytes)
        if sys.platform.startswith("linux"):
            return _apply_linux_cgroup_limit(pid, limit_bytes)
    except (OSError, ValueError, AttributeError, ctypes.ArgumentError) as exc:
        return FallbackMemoryLimitHandle(
            detail=(
                f"hard OS limit unavailable ({type(exc).__name__}); "
                "RSS supervisor active"
            )
        )
    return FallbackMemoryLimitHandle(
        detail=f"unsupported platform {sys.platform}; RSS supervisor active"
    )


__all__ = [
    "FallbackMemoryLimitHandle",
    "ProcessMemoryLimitHandle",
    "apply_process_memory_limit",
]
