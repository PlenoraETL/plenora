"""Offline Python version contract; stdlib only, never installs or contacts services."""

import argparse
from pathlib import Path
import re
import sys
import sysconfig


ROOT = Path(__file__).resolve().parents[1]


def read_version(path):
    version = Path(path).read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"3\.\d+", version):
        raise ValueError(f"{path}: expected CPython major.minor, got {version!r}")
    return version


def check_runtime(version):
    actual = ".".join(map(str, sys.version_info[:2]))
    if sys.implementation.name != "cpython" or actual != version:
        raise ValueError(f"Expected CPython {version}, running {sys.implementation.name} {actual} ({sys.executable})")
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise ValueError("The supported target is standard CPython with GIL, not free-threaded")


def active_text(path):
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


def check_configuration(root=ROOT):
    root = Path(root)
    version = read_version(root / ".python-version")
    tag = "py" + version.replace(".", "")
    required = {
        "backend/Dockerfile": [
            "FROM ubuntu:24.04@sha256:",
            "COPY .python-version /opt/plenora/.python-version",
            '"python=$(cat /opt/plenora/.python-version)"',
            "python /opt/plenora/check_python_version.py --runtime-only --version-file /opt/plenora/.python-version",
        ],
        "backend/Dockerfile.test2": [
            f"FROM plenora-backend:{tag}",
            "COPY .python-version /opt/plenora/.python-version",
            "python /opt/plenora/check_python_version.py --runtime-only --version-file /opt/plenora/.python-version",
        ],
        "backend/test-e2e-v2/fake-rest/Dockerfile": [
            f"FROM python:{version}-bookworm",
            "COPY .python-version /opt/plenora/.python-version",
            "python /opt/plenora/check_python_version.py --runtime-only --version-file /opt/plenora/.python-version",
        ],
        "backend/test-e2e-v2/docker-compose.yml": [
            "context: ../..", "dockerfile: backend/test-e2e-v2/fake-rest/Dockerfile",
        ],
        "docker-compose.yml": [f"image: plenora-backend:{tag}", "dockerfile: backend/Dockerfile"],
        "docker-compose.test2.yml": [f"image: plenora-test2:{tag}", "dockerfile: backend/Dockerfile.test2"],
        "scripts/install.sh": [
            'INSTALLER_REQUIRED_PYTHON_MAJOR_MINOR="$(cat "${REPO_ROOT}/.python-version")"',
            'INSTALLER_PYTHON="python${INSTALLER_REQUIRED_PYTHON_MAJOR_MINOR}"',
            '${INSTALLER_PYTHON} -m venv venv',
        ],
        "scripts/install-native.ps1": [
            "$PythonVersion = (Get-Content -LiteralPath (Join-Path $RepoRoot '.python-version') -Raw).Trim()",
            '"python=$PythonVersion"',
            "--runtime-only --version-file (Join-Path $RepoRoot '.python-version')",
        ],
    }
    for name in ("ci-quality.yml", "ci-cleanroom.yml", "xlsx-exporter-windows.yml"):
        required[f".github/workflows/{name}"] = [
            "python-version-file: .python-version", "run: python scripts/check_python_version.py",
        ]
    errors = []
    for name, markers in required.items():
        text = active_text(root / name)
        for marker in markers:
            if marker not in text:
                errors.append(f"{name}: missing active {marker!r}")
        for pattern in (r"python(?:=|:)(3\.\d+)", r"python-version:\s*[\"']?(3\.\d+)", r"python(3\.\d+)"):
            for match in re.finditer(pattern, text):
                if match[1] != version:
                    errors.append(f"{name}: stale Python {match[1]}, expected {version}")
        if name == "docker-compose.yml":
            images = re.findall(r"image:\s*(plenora-backend:\S+)", text)
            if images != [f"plenora-backend:{tag}"] * 4:
                errors.append(f"{name}: API, both workers and beat must share the versioned image")
    if errors:
        raise ValueError("\n".join(errors))
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--static-only", action="store_true", help="Check configuration on an older offline host")
    mode.add_argument("--runtime-only", action="store_true", help="Check interpreter without a repository checkout")
    parser.add_argument("--version-file", type=Path, default=ROOT / ".python-version")
    args = parser.parse_args()
    try:
        version = read_version(args.version_file)
        if not args.runtime_only:
            check_configuration()
        if not args.static_only:
            check_runtime(version)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Python version contract failed: {exc}\n")
    print(f"Python {version} contract OK ({'configuration only' if args.static_only else 'runtime'})")


if __name__ == "__main__":
    main()
