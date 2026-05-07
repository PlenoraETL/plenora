# Plenora-ETL Documentation

Canonical documentation index for Plenora-ETL.

Historical audit snapshots are preserved under `docs/audits/`; active
product, architecture, development, operations, and compliance documents
live in the sections below.

---

## Product

- [Product overview](product/overview.md)
- [Roadmap](product/roadmap.md)
- [Residual register](product/residual-register.md)

## Architecture

- [Architecture overview](architecture/overview.md)
- [Macro components](architecture/macro-components.md)
- [Horizontal engines](architecture/horizontal-engines.md)
- [Execution engine seam](architecture/execution-engine-seam.md)
- [Invariants](architecture/invariants.md)
- [Inventory report](architecture/inventory_report.md)
- [Architecture decisions](architecture/decisions/README.md)

## Development

- [Onboarding](development/onboarding.md)
- [Backend](development/backend.md)
- [Frontend](development/frontend.md)
- [Contributing](development/contributing.md)
- [Changelog](development/changelog.md)
- [Code map](development/code-map.md)
- [Strategic backlog](development/strategic-backlog.md)

## Operations

- [Clean install, non-Docker](ops/install-non-docker.md)
- [Clean install, Docker scaffold](ops/install-docker.md)
- [Marco Linux dry run pack](ops/install-marco-dry-run.md)
- [First PC validation report template](ops/first-pc-validation-report-template.md)
- [First PC report review checklist](ops/first-pc-report-review-checklist.md)
- [Runtime prerequisites](ops/runtime-prereqs.md)
- [Runbook](ops/runbook.md)
- [Deploy checklist](ops/deploy-checklist.md)
- [Backup and restore](ops/backup-restore.md)
- [DR drill playbook](ops/dr_drill_playbook.md)
- [DR drill log](ops/dr_drill_log.md)
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

## Historical Audits

- [Audits index](audits/README.md)
- [Orchestrate audit snapshot](audits/orchestrate/README.md)

Audit files are historical snapshots. Do not rewrite them as live product
documentation; promote still-valid decisions into ADRs, architecture docs,
or ops runbooks instead.

---

## Documentation Rules

- New canonical docs go under `docs/`, not repo root.
- Root `README.md`, `CHANGELOG.md`, `CODE_MAP.md`, and `CONTRIBUTING.md`
  are entrypoint stubs or GitHub-convention files.
- ADRs are append-only.
- Audit snapshots are append-only unless explicitly archived in a dedicated
  docs cleanup commit.
- Keep canonical docs ASCII-clean unless a file intentionally uses non-ASCII
  domain terminology.
