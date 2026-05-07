# Secrets Management

Scope: Plenora application secrets, their storage, and rotation rules.

Status reviewed: 2026-05-07.

## Canonical Secrets

| Secret | Purpose | Required | Rotation |
|---|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string. | Yes | Rotate on DB credential change or compromise. |
| `PLENORA_ENCRYPTION_KEY` | Fernet key for encrypted application credentials and MFA secrets. | Yes | Not hot-rotatable; requires coordinated re-encryption and downtime. |
| `JWT_SECRET_KEY` | HS256 JWT signing key. | Yes | Rotating invalidates all active JWTs. |
| `CELERY_BROKER_URL` | Redis/Celery broker URL. | Yes | Rotate with Redis credentials/topology. |
| `CELERY_RESULT_BACKEND` | Celery result backend URL. | Yes | Usually same Redis instance as broker. |

Legacy names must not be reintroduced as runtime fallbacks. Historical docs
may mention old aliases; current runtime configuration uses `PLENORA_*`.

## Storage

Development:

- local `backend/.env`;
- file must be gitignored;
- use `chmod 600` on Unix-like hosts.

Production target:

- cloud secret manager or AWS SSM Parameter Store;
- systemd/container runtime injects environment variables;
- application reads secrets at startup.

## Rotation Rules

### `JWT_SECRET_KEY`

Impact: all active sessions become invalid.

Process:

1. Generate a new value.
2. Update the secret store.
3. Restart backend, worker, and beat where needed.
4. Inform users that re-login is required.
5. Review auth logs for the triggering incident.

See [JWT incident response](../ops/jwt-incident-response.md).

### `DATABASE_URL`

Impact: database connectivity interruption during restart.

Process:

1. Create or rotate the DB password.
2. Update the secret store.
3. Verify `psql "$DATABASE_URL" -c "SELECT 1"`.
4. Restart services.
5. Confirm `/ready`.

### `PLENORA_ENCRYPTION_KEY`

Impact: encrypted records become unreadable if rotated without migration.

This key is not hot-rotatable. A valid rotation requires:

- maintenance window;
- old key and new key available to a one-shot migration;
- re-encryption of all encrypted DB fields;
- backup before migration;
- verification after migration;
- service restart with the new key only.

Do not rotate this key as a normal incident reflex unless the compromise
requires it.

## Logging Rules

- Never log secret values.
- Never log full connection strings.
- Fingerprints are allowed only when non-reversible and short.
- Startup logs may say a required secret is missing, but not print the value.

## Release Requirements

Before 1.0:

- all required secrets documented in install docs;
- no committed `.env`;
- no runtime dependency on old alias names;
- incident path for JWT rotation tested or reviewed;
- encryption-key rotation limitation explicitly accepted.

## References

- [Non-Docker install](../ops/install-non-docker.md)
- [Docker install](../ops/install-docker.md)
- [JWT incident response](../ops/jwt-incident-response.md)
- [ADR-0003 Fernet crypto at rest](../architecture/decisions/0003-2026-04-21-fernet-crypto-at-rest.md)
- [ADR-0004 JWT HS256](../architecture/decisions/0004-2026-04-21-jwt-hs256-ttl-8h-no-refresh.md)
