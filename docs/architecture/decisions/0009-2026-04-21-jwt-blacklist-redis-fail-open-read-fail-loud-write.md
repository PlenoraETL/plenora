# ADR-0009: JWT Blacklist In Redis

**Status**: Superseded by Plenora 2.0 (Fase 4, 2026-06-13)

**Date**: 2026-04-21

> **Nota (Plenora 2.0):** lo storage della blacklist è stato migrato da Redis a
> **PostgreSQL** (`kv_store` via `PgKeyValueStore`) con una cache in-memory
> process-local davanti; Redis è stato rimosso dall'intero stack. La semantica
> della decisione resta invariata — `jti` con TTL = scadenza naturale,
> **fail-open in lettura, fail-loud in scrittura** — solo il backend è cambiato.
> Env: `JWT_BLACKLIST_CACHE_TTL_SEC` (era `JWT_BLACKLIST_REDIS_TIMEOUT_SEC`).
> Il testo storico sotto è conservato come record della decisione originale.

## Context

JWT access tokens are stateless until expiry. Logout and administrative token
revocation need a server-side revocation check without introducing refresh
tokens.

## Decision

JWTs include a `jti` claim. Logout stores the `jti` in Redis until the token
would naturally expire. Auth checks consult Redis before accepting a token.

Redis read failures are fail-open to avoid a full application outage from a
temporary blacklist read problem. Redis write failures during logout are
fail-loud because the caller explicitly requested revocation.

## Operational Contract

- Tokens without `jti` are treated as non-revocable compatibility tokens and
  expire by TTL.
- Blacklist keys must expire no later than the JWT TTL.
- Logout must report whether the token was blacklisted.
- Redis errors must be logged without leaking token contents.
- JWT TTL remains governed by [ADR-0004](0004-2026-04-21-jwt-hs256-ttl-8h-no-refresh.md).

## Consequences

Positive:

- Logout has server-side effect without refresh tokens.
- Redis storage is bounded by token TTL.
- A Redis read outage does not block all authenticated traffic.

Negative:

- During Redis read outage, already-issued tokens cannot be checked against the
  blacklist.
- Logout depends on Redis write availability to guarantee revocation.

## References

- `backend/app/core/auth/`
- [ADR-0004](0004-2026-04-21-jwt-hs256-ttl-8h-no-refresh.md)
