# ADR-0009: JWT Blacklist In Redis

**Status**: Accepted

**Date**: 2026-04-21

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
