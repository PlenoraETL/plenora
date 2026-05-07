# ISO 27001 Statement of Applicability

Status reviewed: 2026-05-07.

This is working evidence for Plenora application controls. It is not a
certification claim and must be reviewed before external audit use.

## Scope

In scope:

- backend application;
- worker and beat runtime;
- authentication and authorization;
- application logging and audit trail;
- backup/restore procedures;
- secrets and dependency management documentation.

Out of scope:

- HR controls;
- physical security;
- corporate supplier management;
- customer-specific data classification;
- infrastructure controls owned by the hosting provider or customer.

## Applicability Summary

| ISO area | Status | Evidence |
|---|---|---|
| A.5 Security policies | Org-owned | Corporate policy required outside the repo. |
| A.6 Organization | Partial | RBAC, ADR process, documented review rules. |
| A.7 HR security | Org-owned | Outside application scope. |
| A.8 Asset management | Partial | Architecture docs, resource/storage docs, GDPR register. |
| A.9 Access control | Implemented/Partial | JWT, RBAC, MFA opt-in, tenant/group isolation, admin controls. |
| A.10 Cryptography | Implemented/Partial | Fernet at-rest, JWT signing, documented rotation limits. |
| A.11 Physical security | Provider/Org-owned | Cloud/provider responsibility. |
| A.12 Operations security | Partial | Runbooks, logs, backup/restore, incident docs. |
| A.13 Communications security | Partial | TLS/reverse proxy and trusted proxy assumptions. |
| A.14 Secure development | Partial | ADRs, tests, code review policy, dependency docs. |
| A.15 Supplier relationships | Org-owned | Outside application scope. |
| A.16 Incident management | Partial | Incident response and JWT playbook exist. |
| A.17 Business continuity | Partial | Backup/restore and DR drill docs exist; drill evidence required. |
| A.18 Compliance | Partial | GDPR register, SBOM, secrets docs; formal audit review required. |

## Implemented Application Controls

- JWT authentication with explicit token signing secret.
- Role-based authorization with tenant/group isolation.
- MFA opt-in.
- Redis-backed JWT blacklist and login rate-limiting paths.
- Encrypted storage for application credentials and MFA secrets.
- Audit actions for sensitive operations.
- Health/readiness checks.
- Backup and restore documentation.
- Dependency/SBOM process.
- ADR registry for durable decisions.

## Known Gaps Before External Audit

| Gap | Impact | Required action |
|---|---|---|
| Access review process | Periodic user review is not formalized. | Add operating procedure and evidence template. |
| Mandatory MFA policy | MFA exists but global enforcement policy is product/ops decision. | Decide enforcement for admin/superadmin before certified environments. |
| DR drill evidence | Playbook exists but needs a real run. | Execute and record one drill. |
| SBOM automation | Manual process exists; automation still pending. | Add CI generation or release checklist gate. |
| Container scanning | Not yet canonical. | Add Trivy/Semgrep/Bandit or accepted alternative. |
| Frontend Docker delivery | Docker path incomplete. | Close frontend compose/static delivery gap. |

## References

- [Secrets management](secrets-management.md)
- [SBOM](sbom.md)
- [GDPR processing register](registro-trattamenti-gdpr.md)
- [Incident response](../ops/incident_response.md)
- [JWT incident response](../ops/jwt-incident-response.md)
- [Backup and restore](../ops/backup-restore.md)
- [Release 1.0 readiness](../product/release-1.0.md)
