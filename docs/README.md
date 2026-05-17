# Plenora Documentation

This is the documentation entry point for the current Plenora product.

The rule is simple:

- Canonical docs describe how the system works now.
- ADRs explain durable architecture decisions.

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

## Canonical Architecture Docs

- [Architecture overview](architecture/overview.md)
- [Macro components](architecture/macro-components.md)
- [Horizontal engines](architecture/horizontal-engines.md)
- [Execution engine seam](architecture/execution-engine-seam.md)
- [Invariants](architecture/invariants.md)
- [ADR registry](architecture/decisions/README.md)

Use the architecture overview and ADR registry for current architecture.

## Canonical Development Docs

- [Onboarding](development/onboarding.md)
- [Backend](development/backend.md)
- [Frontend](development/frontend.md)
- [Contributing](development/contributing.md)
- [Code map](development/code-map.md)
- [Strategic backlog](development/strategic-backlog.md)
- [Changelog](development/changelog.md)
- [Backend visual model](development/backend-visual-model.md)
- [Backend horizontalization analysis](backend-horizontalization-analysis.md)
- [Historical audits](audits/README.md)

## Canonical Operations Docs

- [Clean install, non-Docker](ops/install-non-docker.md)
- [Clean install, Docker](ops/install-docker.md)
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

## Compliance Docs

- [Secrets management](compliance/secrets-management.md)
- [SBOM](compliance/sbom.md)
- [ISO 27001 Statement of Applicability](compliance/soa-iso-27001.md)
- [GDPR processing register](compliance/registro-trattamenti-gdpr.md)

Compliance documents are working evidence. They need product-owner review
before being used in an external audit.

## Inventory Policy

The repository intentionally still contains many Markdown files, but they
fall into distinct classes:

- root convention files for GitHub entry points;
- canonical product, architecture, development, operations, and compliance
  docs under `docs/`;
- ADRs, which are append-only;
- local README files next to backend, deploy, script, and test packages;
- generated or historical test-catalog material under `backend/test-e2e/`.

Local package docs that are intentionally kept outside `docs/`:

- [Backend deploy backup runbook](../backend/deploy/backup-runbook.md)
- [Backend deploy IAM](../backend/deploy/iam/README.md)
- [Backend deploy systemd](../backend/deploy/systemd/README.md)
- [Backend deploy Terraform](../backend/deploy/terraform/README.md)
- [Deploy profiles](../deploy/profiles/README.md)
- [Backend tests2](../backend/tests2/README.md)
- [Backend functional tests](../backend/tests2/functional/README.md)
- [Backend integration tests](../backend/tests2/integration/README.md)
- [Backend performance tests](../backend/tests2/performance/README.md)
- [Frontend unit/contract tests](../frontend/tests/README.md)
- [Frontend Playwright tests](../frontend/e2e/README.md)
- [Backend E2E harness](../backend/test-e2e/README.md)
- [Backend E2E runner](../backend/test-e2e/runner/README.md)
- [Backend E2E catalog](../backend/test-e2e/catalog/README.md)
- [Backend E2E docker stack](../backend/test-e2e/docker/README.md)
- [Backend E2E coverage](../backend/test-e2e/coverage/README.md)
- [Backend E2E SQL fixtures](../backend/test-e2e/sql/README.md)
- [Backend E2E expected SQL outputs](../backend/test-e2e/sql/expected/README.md)
- [Backend E2E fake REST fixture](../backend/test-e2e/fake-rest/README.md)
- [Backend E2E file fixtures](../backend/test-e2e/files/README.md)
- [Backend performance query notes](../backend/scripts/perf_queries/README.md)

Historical or generated Markdown that should not be edited as live guidance:

- [Legacy E2E monolith](../backend/test-e2e/test-e2e_LEGACY.md): historical
  backup before the YAML catalog split.
- [Rendered E2E catalog](../backend/test-e2e/test-e2e.md): generated from
  `backend/test-e2e/catalog/*.yaml`.
- [E2E bootstrap procedure](../backend/test-e2e/procedura.md): planning note
  for building the executable runner, not a release checklist.

Unlinked planning notes should be deleted or promoted into a canonical doc.

## Documentation Rules

- Do not add new root-level documentation unless it is a GitHub convention
  file (`README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `CODE_MAP.md`).
- Do not add broad "notes" files. Pick product, architecture, development,
  ops, or compliance.
- Do not rewrite ADRs. Add a new ADR or a superseding note.
- Keep live docs short enough to read and current enough to trust.
