# Plenora Documentation

This is the documentation entry point for the current Plenora product.

The rule is simple:

- Canonical docs describe how the system works now.
- ADRs explain why durable architecture decisions were made.
- Audits are historical evidence and must not be treated as live guidance.

## Start Here

| Need | Read |
|---|---|
| Product shape | [Product overview](product/overview.md) |
| 1.0 readiness | [Release 1.0 readiness](product/release-1.0.md) |
| Backend architecture | [Architecture overview](architecture/overview.md) |
| Horizontal engine map | [Horizontal engines](architecture/horizontal-engines.md) |
| Backend development | [Backend guide](development/backend.md) |
| Frontend development | [Frontend guide](development/frontend.md) |
| Clean install, single machine | [Non-Docker install](ops/install-non-docker.md) |
| Clean install, Docker | [Docker install](ops/install-docker.md) |

## Canonical Product Docs

- [Product overview](product/overview.md)
- [Release 1.0 readiness](product/release-1.0.md)
- [Roadmap](product/roadmap.md)
- [Residual register](product/residual-register.md)

## Canonical Architecture Docs

- [Architecture overview](architecture/overview.md)
- [Macro components](architecture/macro-components.md)
- [Horizontal engines](architecture/horizontal-engines.md)
- [Execution engine seam](architecture/execution-engine-seam.md)
- [Invariants](architecture/invariants.md)
- [ADR registry](architecture/decisions/README.md)

`architecture/inventory_report.md` is historical inventory evidence. Use it
for archaeology, not for the current architecture map.

## Canonical Development Docs

- [Onboarding](development/onboarding.md)
- [Backend](development/backend.md)
- [Frontend](development/frontend.md)
- [Contributing](development/contributing.md)
- [Code map](development/code-map.md)
- [Strategic backlog](development/strategic-backlog.md)
- [Changelog](development/changelog.md)

## Canonical Operations Docs

- [Clean install, non-Docker](ops/install-non-docker.md)
- [Clean install, Docker](ops/install-docker.md)
- [First PC validation report template](ops/first-pc-validation-report-template.md)
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

Operational checklists that mention first-PC validation are executable test
packs, not product architecture.

## Compliance Docs

- [Secrets management](compliance/secrets-management.md)
- [SBOM](compliance/sbom.md)
- [ISO 27001 Statement of Applicability](compliance/soa-iso-27001.md)
- [GDPR processing register](compliance/registro-trattamenti-gdpr.md)

Compliance documents are working evidence. They need product-owner review
before being used in an external audit.

## Historical Audits

- [Audits index](audits/README.md)
- [Orchestrate audit snapshot](audits/orchestrate/README.md)

The audit tree is read-only history. If an old audit still contains useful
truth, promote that truth into a canonical doc or a new ADR.

## Documentation Rules

- Do not add new root-level documentation unless it is a GitHub convention
  file (`README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `CODE_MAP.md`).
- Do not add broad "notes" files. Pick product, architecture, development,
  ops, or compliance.
- Do not rewrite ADR history. Add a new ADR or a superseding note.
- Keep live docs short enough to read and current enough to trust.
