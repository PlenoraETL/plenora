# ADR-0021: Environment And Runtime Configuration Boundary

**Status**: Accepted

**Date**: 2026-05-03

## Context

Plenora uses environment variables for secrets, deployment wiring, application
policy, operational tuning, feature flags, and tests. Without classification,
new settings tend to spread through direct `os.getenv` calls and duplicate
defaults.

## Decision

Every runtime setting belongs to one category:

| Category | Meaning | Runtime owner |
|---|---|---|
| `SECRET_INFRA` | Secrets such as encryption keys, JWT keys, credentials | env only, never logged |
| `DEPLOY_INFRA` | Hostnames, ports, DB URLs, Redis URLs, object-storage endpoints | env / deployment config |
| `APP_POLICY` | Product policy such as retention, storage policy, default persistence mode | settings boundary |
| `OPERATIONAL_TUNING` | Timeouts, pool sizes, batch sizes, memory caps | settings boundary |
| `FEATURE_FLAG` | Explicit runtime feature switches | settings boundary with owner and removal rule |
| `TEST_ONLY` | Test harness controls | tests only |
| `RETIRED_COMPAT` | Old env names kept only when an ADR gives a removal rule | env fallback only when documented |

New settings must be routed through the settings boundary unless they are
secrets or deployment infrastructure that intentionally remain env-only.

## Rules

- Do not add direct `os.getenv` calls in domain code when `settings.get()` or a
  typed helper already owns the value.
- Secrets must not have non-secret fallbacks and must not be logged.
- Old env names require an ADR reference and a removal condition.
- Feature flags require an owner, default, and removal or stabilization rule.
- Test-only settings must not affect production behavior.
- Runtime policy should move toward typed config, but only when the loader and
  schema preserve current deployment behavior.

## Consequences

Positive:

- Configuration ownership is explicit.
- Secret handling stays separate from normal policy and tuning.
- Future typed settings work has a stable classification model.

Negative:

- Some values still pass through both `settings.get()` and direct env reads
  until their owners are consolidated.
- Retired compatibility names need periodic review.

## References

- `backend/app/config.py`
- `backend/app/config/storage_config.py`
- [ADR-0003](0003-2026-04-21-fernet-crypto-at-rest.md)
- [ADR-0015](0015-2026-04-22-minio-object-storage-provider.md)
