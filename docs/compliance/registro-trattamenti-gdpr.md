# GDPR Processing Register

Status reviewed: 2026-05-07.

This is a product-level processing register for Plenora. The customer that
deploys Plenora remains the data controller for business data loaded into
pipelines.

This document is working evidence, not legal advice.

## Scope

Covered:

- user accounts;
- authentication and MFA data;
- audit trail;
- application logs;
- JWT/session revocation metadata;
- encrypted connection credentials stored so pipelines can run.

Not covered:

- customer business datasets processed through ETL pipelines;
- customer-specific legal bases;
- customer-specific retention rules;
- third-party systems connected by the customer.

## Processing Activities

| ID | Activity | Purpose | Typical data |
|---|---|---|---|
| T1 | User authentication | Access control | username, password hash, role, group, active flag, last login metadata. |
| T2 | MFA TOTP | Stronger login security | encrypted TOTP secret, enrollment metadata, verification metadata. |
| T3 | Audit trail | Accountability and security review | user id, username, action, resource id, client IP, user agent, timestamp. |
| T4 | Application logs | Troubleshooting and incident response | request id, logger, status, error class, operational metadata. |
| T5 | JWT/session revocation | Session continuity and logout/revocation | token id, expiry metadata, blacklist entries. |
| T6 | Connection credentials | Execute customer-configured pipelines | encrypted DB/API/provider credentials. |

## Roles

| Actor | Role |
|---|---|
| Customer deploying Plenora | Data controller for users and business datasets. |
| Plenora application | Processor/tooling layer, depending on contract. |
| Hosting provider | Infrastructure processor/sub-processor, depending on deployment. |
| External systems connected by user | Separate controllers/processors determined by customer. |

## Security Measures

- Passwords stored as hashes, never plaintext.
- Sensitive application credentials encrypted at rest.
- JWT signing secret separated from encryption key.
- Role and group checks on backend endpoints.
- Superadmin bypasses must be explicit and auditable.
- Audit trail for sensitive operations.
- Health/readiness and incident response documentation.
- Backup/restore process documented.

## Retention

Default retention is deployment-specific and must be finalized by the
customer/controller.

Recommended baseline:

| Data | Suggested retention |
|---|---|
| User account | Life of account, then delete or disable according to customer policy. |
| Audit trail | At least 24 months unless customer policy requires otherwise. |
| Application logs | 30-180 days depending on operational need and storage policy. |
| JWT blacklist | Until token expiry. |
| Connection credentials | Until connection deletion or credential rotation. |
| Pipeline business data | Customer-defined. |

## Data Subject Rights

The product should support the customer in handling:

- access requests;
- account deletion or disablement;
- credential removal;
- audit export where legally appropriate.

Business data in pipeline sources/results remains the customer's
responsibility to classify, export, and delete according to the deployment
contract.

## Open Items Before External Audit

| Item | Action |
|---|---|
| Customer-specific legal bases | Fill per deployment. |
| Final retention policy | Decide per customer and configure cleanup where needed. |
| DPA/sub-processor list | Attach contract-specific list. |
| DPIA | Required when customer processes high-risk personal data through pipelines. |
| Log minimization review | Confirm no secrets or excessive personal data are logged. |

## References

- [Secrets management](secrets-management.md)
- [ISO 27001 Statement of Applicability](soa-iso-27001.md)
- [Incident response](../ops/incident_response.md)
- [Release 1.0 readiness](../product/release-1.0.md)
