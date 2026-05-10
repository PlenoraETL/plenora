# ADR-0003: Fernet Crypto At Rest

**Status**: Accepted

**Date**: 2026-04-21

## Context

Plenora persists user-managed credentials and secrets for remote databases,
external APIs, datasources, and MFA. A database dump or read-only database
access must not expose those secrets in plaintext.

The application needs idempotent encryption helpers for both single values and
selected fields inside JSON-like config dictionaries.

## Decision

Use `cryptography.Fernet` for application-level encryption at rest.

The crypto module owns:

- `encrypt`
- `decrypt`
- `is_encrypted`
- `encrypt_dict_fields`
- `decrypt_dict_fields`
- test cache reset helpers

The canonical runtime key is `PLENORA_ENCRYPTION_KEY`. JWT secrets are separate
and must never be used as encryption-key fallback.

Encrypted values use the `enc:` prefix. Encryption is idempotent: values that
already carry the prefix are returned unchanged.

## Operational Contract

- Production must fail fast when `PLENORA_ENCRYPTION_KEY` is missing.
- Development and test may use an explicit test/default path only when the
  code logs that behavior clearly.
- Old env aliases are retired and must not be reintroduced as runtime fallback.
- Plaintext reads may be tolerated only as compatibility reads for records that
  predate encryption; new writes must encrypt sensitive fields.
- Secret fields must be selected by whitelist, not by broad recursive guessing.
- Key rotation is an explicit maintenance operation because existing records
  must be re-encrypted.

## Rationale

Fernet provides authenticated encryption with a stable Python implementation
and avoids hand-written nonce/tag handling. The input key is expected to be
high entropy, so deterministic SHA-256 derivation into Fernet key material is
adequate for this application-level secret.

Keeping the encryption key separate from JWT signing keys prevents token
rotation from invalidating encrypted records.

## Consequences

Positive:

- One auditable crypto-at-rest boundary.
- Idempotent saves do not double-encrypt values.
- The `enc:` prefix makes encrypted values recognizable during operations.

Negative:

- Key rotation requires a planned re-encryption procedure.
- Secrets still exist in process environment/configuration at runtime and must
  be protected by host-level controls.

## References

- `backend/app/infrastructure/security/crypto.py`
- [ADR-0004](0004-2026-04-21-jwt-hs256-ttl-8h-no-refresh.md)
- [ADR-0021](0021-2026-05-03-env-config-boundary.md)
