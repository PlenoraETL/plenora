# Plenora Documentation

This is the documentation entry point for the current Plenora product.

The rule is simple: canonical docs describe how the system works now. Historical
notes, ADRs, and audit snapshots are not maintained as live Markdown; Git
history preserves that context.

## Start Here

| Need | Read |
|---|---|
| Product shape | [Product overview](product/overview.md) |
| 1.0 readiness | [Release 1.0 readiness](product/release-1.0.md) |
| Backend architecture | [Architecture overview](architecture/overview.md) |
| Horizontal engine map | [Horizontal engines](architecture/horizontal-engines.md) |
| Backend development | [Backend guide](development/backend.md) |
| Frontend development | [Frontend guide](development/frontend.md) |
| Clean install, Windows native | [Windows native install](ops/install-windows.md) |
| Clean install, single machine | [Non-Docker install](ops/install-non-docker.md) |
| Clean install, Docker | [Docker install procedures](ops/install-docker.md) |
| Data-plane memory isolation | [Data-plane subprocess](ops/data-plane-subprocess.md) |
| Clean install, single-server Docker | [Single-server Docker install](ops/install-docker-single-server.md) |
| Clean install, multi-server Docker | [Multi-server Docker install](ops/install-docker-multi-server.md) |

## Product

- [Product overview](product/overview.md)
- [Release 1.0 readiness](product/release-1.0.md)

## Architecture

- [Architecture overview](architecture/overview.md)
- [Macro components](architecture/macro-components.md)
- [Horizontal engines](architecture/horizontal-engines.md)
- [Execution engine seam](architecture/execution-engine-seam.md)
- [Invariants](architecture/invariants.md)

## Development

- [Onboarding](development/onboarding.md)
- [Backend](development/backend.md)
- [Frontend](development/frontend.md)
- [Contributing](development/contributing.md)
- [Code map](development/code-map.md)
- [Bug marker ledger](development/bug-ledger.md)

## Operations

- [Windows native install](ops/install-windows.md) ([PDF](ops/install-windows.pdf))
- [Clean install, non-Docker](ops/install-non-docker.md)
- [Docker install procedures](ops/install-docker.md)
- [Clean install, single-server Docker](ops/install-docker-single-server.md)
- [Clean install, multi-server Docker](ops/install-docker-multi-server.md)
- [Runtime prerequisites](ops/runtime-prereqs.md)
- [Runbook](ops/runbook.md)
- [Deploy checklist](ops/deploy-checklist.md)
- [Backup and restore](ops/backup-restore.md)
- [DR drill playbook](ops/dr_drill_playbook.md)
- [Incident response](ops/incident_response.md)
- [JWT incident response](ops/jwt-incident-response.md)
- [Trusted proxies](ops/trusted-proxies.md)
- [Tickets](ops/tickets.md)
- [Handoff](ops/handoff.md)

## Compliance

- [Secrets management](compliance/secrets-management.md)
- [SBOM](compliance/sbom.md)
- [ISO 27001 Statement of Applicability](compliance/soa-iso-27001.md)
- [GDPR processing register](compliance/registro-trattamenti-gdpr.md)

Compliance documents are working evidence. They need product-owner review
before being used in an external audit.

## Repository Markdown Policy

- Current product, architecture, development, operations, and compliance docs
  live under `docs/`.
- Root Markdown is limited to conventional entry points: `README.md` and
  `CONTRIBUTING.md`.
- Local `README.md` files are allowed only next to executable test, deploy, or
  fixture packages where moving them would make the package harder to operate.
- Historical notes, ADRs, superseded audits, and broad scratch files should be
  deleted instead of archived in the repository.
- When a durable rule changes, update the current canonical doc in the same
  change.
