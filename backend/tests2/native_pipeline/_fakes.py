"""Fake pinned toolchain used by the native_pipeline tests.

The three fakes are real executable scripts with shebang and mode 0700, so
each production-shaped argv contains exactly one absolute executable before
its stage arguments. Environment
scrubbing, exit codes, stream bounds and timeouts are exercised against a
genuine child process rather than a mock.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from app.core.native_pipeline import (
    NativePipelineRequest,
    NativeToolchainConfig,
    PinnedBinary,
    PipelineOperation,
    SourceKind,
    TableReadSpec,
)


DSN_ENV_VAR = "PLENORA_NATIVE_TEST_DSN"
DSN_VALUE = "postgresql://native_user:sup3r-s3cret@db.internal:5432/plenora"

DATABASE_VERSION = "1.1.0"
DATA_VERSION = "1.0.2"
IO_VERSION = "1.0.1"

DATABASE_IDENTITY = "plenora-database"
DATA_IDENTITY = "plenora-data-tools"
IO_IDENTITY = "plenora-io"

DATABASE_PAYLOAD = "ARROW-IPC-FAKE:database"
DATA_PAYLOAD = "ARROW-IPC-FAKE:data"
IO_PAYLOAD = "ARROW-IPC-FAKE:io"


_FAKE_TOOL_SOURCE = '''\
#!/usr/bin/env python3
"""Behaviour-driven fake for a pinned Plenora native tool."""

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__ROOT__)
TOOL = __TOOL__
ARGS = sys.argv[1:]
BEHAVIOUR = json.loads((HERE / "behaviour.json").read_text(encoding="utf-8"))[TOOL]

record = {"tool": TOOL, "args": ARGS, "env": dict(os.environ)}
if TOOL == "data" and "--plan" in ARGS:
    plan = Path(ARGS[ARGS.index("--plan") + 1])
    record["plan_bytes"] = plan.read_bytes().hex()
with (HERE / "invocations.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(
        json.dumps(record) + "\\n"
    )

if ARGS[:1] == ["--version"]:
    version_sleep_ms = int(BEHAVIOUR.get("version_sleep_ms", 0))
    if version_sleep_ms:
        time.sleep(version_sleep_ms / 1000.0)
    sys.stdout.write(str(BEHAVIOUR.get("version_line", "")) + "\\n")
    raise SystemExit(int(BEHAVIOUR.get("version_exit", 0)))

sleep_ms = int(BEHAVIOUR.get("sleep_ms", 0))
if sleep_ms:
    time.sleep(sleep_ms / 1000.0)

mutate_path = BEHAVIOUR.get("mutate_path")
if mutate_path:
    target = Path(mutate_path)
    if BEHAVIOUR.get("remove_mutated_path"):
        target.unlink()
    else:
        target.write_bytes(str(BEHAVIOUR.get("mutate_content", "changed")).encode())

for name in BEHAVIOUR.get("echo_env", []):
    sys.stderr.write("connected with %s=%s\\n" % (name, os.environ.get(name, "")))


def _output_path():
    if "--output" in ARGS:
        return ARGS[ARGS.index("--output") + 1]
    if TOOL == "database":
        return ARGS[4]
    if TOOL == "io":
        return ARGS[2]
    return None


content = BEHAVIOUR.get("output_content")
if content is not None:
    Path(_output_path()).write_bytes(content.encode("utf-8"))
output_size_bytes = int(BEHAVIOUR.get("output_size_bytes", 0))
if output_size_bytes:
    Path(_output_path()).write_bytes(b"x" * output_size_bytes)
post_output_sleep_ms = int(BEHAVIOUR.get("post_output_sleep_ms", 0))
if post_output_sleep_ms:
    time.sleep(post_output_sleep_ms / 1000.0)
symlink_target = BEHAVIOUR.get("output_symlink_target")
if symlink_target:
    output = Path(_output_path())
    if output.exists() or output.is_symlink():
        output.unlink()
    output.symlink_to(symlink_target)

stdout_text = BEHAVIOUR.get("stdout")
if stdout_text:
    sys.stdout.write(stdout_text)
stdout_fill = int(BEHAVIOUR.get("stdout_fill_bytes", 0))
if stdout_fill:
    sys.stdout.write("x" * stdout_fill)
    sys.stdout.flush()

stderr_text = BEHAVIOUR.get("stderr")
if stderr_text:
    sys.stderr.write(stderr_text)
stderr_fill = int(BEHAVIOUR.get("stderr_fill_bytes", 0))
if stderr_fill:
    sys.stderr.write("y" * stderr_fill)
    sys.stderr.flush()

raise SystemExit(int(BEHAVIOUR.get("exit_code", 0)))
'''


def _default_behaviour() -> dict[str, Any]:
    return {
        "database": {
            "version_line": f"{DATABASE_IDENTITY} {DATABASE_VERSION}",
            "output_content": DATABASE_PAYLOAD,
            "stdout": json.dumps({"rows": 3, "diagnostics": []}),
            "exit_code": 0,
        },
        "data": {
            "version_line": f"{DATA_IDENTITY} {DATA_VERSION}",
            "output_content": DATA_PAYLOAD,
            "stdout": json.dumps({"accepted_rows": 3, "diagnostics": []}),
            "exit_code": 0,
        },
        "io": {
            "version_line": f"{IO_IDENTITY} {IO_VERSION}",
            "output_content": IO_PAYLOAD,
            "stdout": json.dumps({"loss": {"declared": False, "entries": []}}),
            "exit_code": 0,
        },
    }


@dataclass(frozen=True)
class FakeToolchain:
    """Filesystem-backed fake toolchain with a per-run invocation ledger."""

    root: Path

    @property
    def script(self) -> Path:
        """Compatibility alias for the database executable."""
        return self.executable("database")

    def executable(self, tool: str) -> Path:
        return (self.root / f"fake-{tool}").resolve()

    @property
    def behaviour_path(self) -> Path:
        return self.root / "behaviour.json"

    @property
    def log_path(self) -> Path:
        return self.root / "invocations.jsonl"

    def argv_prefix(self, tool: str) -> tuple[str, ...]:
        return (str(self.executable(tool)),)

    def configure(self, tool: str, **changes: Any) -> None:
        behaviour = json.loads(self.behaviour_path.read_text(encoding="utf-8"))
        behaviour[tool].update(changes)
        self.behaviour_path.write_text(
            json.dumps(behaviour), encoding="utf-8"
        )

    def invocations(self) -> list[Mapping[str, Any]]:
        if not self.log_path.is_file():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def stage_invocations(self) -> list[Mapping[str, Any]]:
        """Invocations that are not identity probes."""
        return [
            entry for entry in self.invocations() if entry["args"][:1] != ["--version"]
        ]

    def tools_spawned(self) -> list[str]:
        return [str(entry["tool"]) for entry in self.invocations()]


def build_fake_toolchain(root: Path) -> FakeToolchain:
    root.mkdir(parents=True, exist_ok=True)
    fakes = FakeToolchain(root=root)
    for tool in ("database", "data", "io"):
        executable = fakes.executable(tool)
        executable.write_text(
            _FAKE_TOOL_SOURCE.replace("__TOOL__", repr(tool)).replace(
                "__ROOT__", repr(str(root.resolve()))
            ),
            encoding="utf-8",
        )
        executable.chmod(0o700)
    fakes.behaviour_path.write_text(
        json.dumps(_default_behaviour()), encoding="utf-8"
    )
    return fakes


def build_config(fakes: FakeToolchain, **overrides: Any) -> NativeToolchainConfig:
    fields: dict[str, Any] = {
        "enabled": True,
        "database": PinnedBinary(
            argv_prefix=fakes.argv_prefix("database"),
            identity=DATABASE_IDENTITY,
            version=DATABASE_VERSION,
        ),
        "data": PinnedBinary(
            argv_prefix=fakes.argv_prefix("data"),
            identity=DATA_IDENTITY,
            version=DATA_VERSION,
        ),
        "io": PinnedBinary(
            argv_prefix=fakes.argv_prefix("io"),
            identity=IO_IDENTITY,
            version=IO_VERSION,
        ),
        "stage_timeout_ms": 60_000,
        "identity_timeout_ms": 30_000,
    }
    fields.update(overrides)
    return NativeToolchainConfig(**fields)


def build_request(
    workdir: Path,
    *,
    output_name: str = "final.arrow",
    source_kind: SourceKind = SourceKind.POSTGRES,
    operation: PipelineOperation = PipelineOperation.READ,
    order_by: str | None = "id",
) -> NativePipelineRequest:
    plan_path = workdir / "plan.json"
    if not plan_path.exists():
        plan_path.write_text(
            json.dumps({"version": 1, "ops": [{"op": "filter"}]}), encoding="utf-8"
        )
    return NativePipelineRequest(
        source=TableReadSpec(
            dsn_env_var=DSN_ENV_VAR,
            schema="public",
            table="misure",
            max_rows=10_000,
            max_output_bytes=64 * 1024 * 1024,
            order_by=order_by,
        ),
        plan_path=plan_path,
        output_path=workdir / output_name,
        source_kind=source_kind,
        operation=operation,
    )
