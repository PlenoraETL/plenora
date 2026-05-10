# Architecture Decision Records

This directory contains accepted architecture decisions for Plenora. ADRs are
kept as decision records, not as implementation logs.

## Format

Use this shape for new records:

```markdown
# ADR-NNNN: Short Title

**Status**: Accepted | Superseded by ADR-NNNN | Deprecated

**Date**: YYYY-MM-DD

## Context

Problem, constraints, and tradeoffs.

## Decision

The chosen architecture rule.

## Consequences

Positive and negative effects.

## References

Relevant code, docs, tests, or related ADRs.
```

## Rules

- Do not delete accepted ADRs just because implementation is complete.
- Supersede an ADR when the decision changes.
- Keep ADRs concise enough to explain the current rule and why it exists.
- Put operational how-to material in development or ops docs, not in ADRs.

## Index

### Current Direction

- [ADR-0023](0023-2026-05-06-arcgis-external-provider-boundary.md) - ArcGIS is an external read/write provider, not an IMDF subdomain.
- [ADR-0024](0024-2026-05-07-self-documenting-runtime-code.md) - Runtime code readability policy.

### Security / Crypto

- [ADR-0001](0001-2026-04-17-eval-whitelist-over-asteval.md) - Formula validation through AST whitelist.
- [ADR-0002](0002-2026-04-21-safe-number-identifier-vs-bindparam.md) - Safe SQL number/identifier handling.
- [ADR-0003](0003-2026-04-21-fernet-crypto-at-rest.md) - Fernet crypto at rest.
- [ADR-0004](0004-2026-04-21-jwt-hs256-ttl-8h-no-refresh.md) - JWT HS256, 8h TTL, no refresh token.
- [ADR-0009](0009-2026-04-21-jwt-blacklist-redis-fail-open-read-fail-loud-write.md) - JWT JTI blacklist in Redis.
- [ADR-0010](0010-2026-04-21-trusted-proxies-allowlist.md) - Trusted proxy allowlist.
- [ADR-0018](0018-2026-04-30-mfa-required-disclosure-tradeoff.md) - MFA required disclosure behavior.
- [ADR-0022](0022-2026-05-04-auth-disclosure-policy.md) - Auth disclosure policy and domain error codes.

### Architecture Patterns

- [ADR-0005](0005-2026-04-21-async-user-recheck-helper.md) - Shared async user authorization recheck helper.
- [ADR-0006](0006-2026-04-21-crs-opt-in-default-same.md) - CRS opt-in metric behavior.
- [ADR-0007](0007-2026-04-21-protocol-runtime-checkable-vs-abc.md) - Runtime-checkable protocols over ABCs for storage.
- [ADR-0008](0008-2026-04-21-backup-observability-db-side.md) - Backup observability stored in DB.
- [ADR-0011](0011-2026-04-21-file-sources-dispatch-s3-threshold.md) - File source dispatch and S3 threshold.
- [ADR-0012](0012-2026-04-21-preview-execute-policy.md) - Preview vs execute policy.
- [ADR-0014](0014-2026-04-21-log-archive-pipeline.md) - Log archive pipeline.
- [ADR-0015](0015-2026-04-22-minio-object-storage-provider.md) - MinIO object storage provider.
- [ADR-0016](0016-2026-04-23-imdf-boundaries.md) - IMDF module boundaries.
- [ADR-0017](0017-2026-04-24-features-vertical-status.md) - Features vertical status.
- [ADR-0019](0019-2026-05-01-intermediate-mode-per-call-override.md) - Per-call intermediate persistence override.
- [ADR-0020](0020-2026-05-01-source-bound-test-keep-list.md) - Source-bound test keep policy.
- [ADR-0021](0021-2026-05-03-env-config-boundary.md) - Environment and runtime configuration boundary.

### External Providers

- [ADR-0023](0023-2026-05-06-arcgis-external-provider-boundary.md) - ArcGIS provider boundary.

### Runtime Code Quality

- [ADR-0024](0024-2026-05-07-self-documenting-runtime-code.md) - Self-documenting runtime code policy.
