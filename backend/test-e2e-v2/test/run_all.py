"""Run every numbered functional E2E v2 host test."""

from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FUNCTIONAL_ROOT = ROOT / "functional"
ENV_FILES = (
    ROOT.parent / ".env",
    ROOT / ".env",
)


def _load_env_file() -> None:
    for env_file in ENV_FILES:
        if not env_file.exists():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def main() -> int:
    _load_env_file()

    if not os.environ.get("PLENORA_E2E_TOKEN") and not (
        os.environ.get("PLENORA_E2E_USERNAME")
        and os.environ.get("PLENORA_E2E_PASSWORD")
    ):
        print(
            "Missing auth. Set PLENORA_E2E_TOKEN or "
            "PLENORA_E2E_USERNAME + PLENORA_E2E_PASSWORD.",
            file=sys.stderr,
        )
        return 2

    tests = sorted(FUNCTIONAL_ROOT.glob("test_*.py"))
    if not tests:
        print(f"No functional tests found in {FUNCTIONAL_ROOT}.")
        return 1

    failed: list[Path] = []
    for test in tests:
        print(f"\n==> functional/{test.name}", flush=True)
        result = subprocess.run([sys.executable, str(test)], cwd=str(FUNCTIONAL_ROOT))
        if result.returncode != 0:
            failed.append(test)

    if failed:
        print("\nFAILED:")
        for test in failed:
            print(f"  {test.name}")
        return 1

    print("\nAll E2E v2 host tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
