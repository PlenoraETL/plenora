# =============================================================================
# Plenora-ETL - Installer Windows nativo (forwarder)
# =============================================================================
#
# Il percorso nativo Windows E' SUPPORTATO (collaudato su Windows Server
# 2025, 2026-07-04): stack completo PostgreSQL + RabbitMQ + worker
# Dramatiq + beat APScheduler + MinIO opzionale + frontend via Caddy,
# tutto come servizi Windows. Vedi docs/ops/install-windows.md.
#
# Questo file inoltra a scripts/install-native.ps1 (l'implementazione).
# NB: NIENTE param()/CmdletBinding qui — lo splatting speciale di @args
# preserva l'accoppiamento nome-parametro/valore; un array di stringhe
# splattato verrebbe invece ri-applicato POSIZIONALMENTE (visto sul
# campo: -SkipAdminBootstrap finiva dentro -WorkerProcesses).
#
# Storia: fino al 2026-07-04 era uno stub che rimandava a WSL2 — la
# migrazione Plenora 2.0 (Celery+Redis -> Dramatiq+RabbitMQ+PostgreSQL)
# ha reso possibile il nativo.
# =============================================================================

& (Join-Path $PSScriptRoot 'install-native.ps1') @args
exit $LASTEXITCODE
