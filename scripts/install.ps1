# =============================================================================
# Plenora-ETL - Windows installer STUB (intentionally not implemented yet)
# =============================================================================
#
# BE-GO-READY-INSTALLATION-009-INSTALL-SCRIPT-PROTOTYPE.
#
# This is a STUB. The Linux installer (`scripts/install.sh`) is the
# canonical reference. A full Windows / WSL installer is out of scope
# for the prototype: PowerShell and bash diverge on every primitive
# (process spawning, signal handling, env propagation, file modes,
# atomic mv vs Move-Item) and a half-finished Windows path would risk
# leaving secrets in a half-written file or running with permissive
# ACLs.
#
# Until the Windows path is bounded:
#   - the systemd surface (plenora-backend.service, plenora-celery.service,
#     plenora-beat.service) does not exist on Windows. The deployment
#     target is Linux (Ubuntu 24.04 reference);
#   - operators on Windows hosts use WSL2 and run the bash installer
#     inside the WSL distribution. WSL exposes `/mnt/c/...` so the
#     repo can stay on the Windows filesystem if needed (but PG/RabbitMQ
#     should run inside the WSL distro for performance).
#
# To proceed today on Windows:
#   1. Open a WSL2 terminal (Ubuntu 24.04 recommended);
#   2. cd to the repo root inside WSL;
#   3. Run: bash scripts/install.sh
#
# Tracking: a future stream will decide whether a real PowerShell
# installer is justified once production deployments on Windows
# actually exist. Today the only Windows usage is dev (and dev does
# not need this script at all - `python -m uvicorn` from a virtualenv
# is enough).
#
# =============================================================================

[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

Write-Host ''
Write-Host '==============================================================' -ForegroundColor Yellow
Write-Host 'Plenora-ETL - Windows installer is not implemented yet.' -ForegroundColor Yellow
Write-Host '==============================================================' -ForegroundColor Yellow
Write-Host ''
Write-Host 'The canonical installer is the Linux script:' -ForegroundColor Cyan
Write-Host '  scripts/install.sh' -ForegroundColor Cyan
Write-Host ''
Write-Host 'On Windows, run the script from inside WSL2:' -ForegroundColor Cyan
Write-Host '  1. wsl --install -d Ubuntu-24.04   # one-time' -ForegroundColor White
Write-Host '  2. wsl                              # enter the distro' -ForegroundColor White
Write-Host '  3. cd /path/to/plenora-etl' -ForegroundColor White
Write-Host '  4. bash scripts/install.sh' -ForegroundColor White
Write-Host ''
Write-Host 'WSL2 prerequisites inside the Ubuntu distro:' -ForegroundColor Cyan
Write-Host '  - Python 3.11, Node LTS, postgresql-client, rabbitmq-server, curl, git' -ForegroundColor White
Write-Host '  - See docs/ops/install-non-docker.md section1 for the apt one-liner.' -ForegroundColor White
Write-Host ''
Write-Host 'Native Windows is NOT supported as a deployment target:' -ForegroundColor Cyan
Write-Host '  - the systemd surface (plenora-backend.service, plenora-celery.service,' -ForegroundColor White
Write-Host '    plenora-beat.service) does not exist on Windows;' -ForegroundColor White
Write-Host '  - the production target is Linux (Ubuntu 24.04 reference).' -ForegroundColor White
Write-Host ''

if ($Force) {
    Write-Host '-Force was passed. The stub still does nothing - it is intentional.' -ForegroundColor Yellow
}

exit 1
