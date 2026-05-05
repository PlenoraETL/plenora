"""Stable contract boundaries — pure types and policy primitives.

Modules under `app.shared.contracts` are RE-EXPORT-ONLY or PURE.
They MUST NOT import:
- `fastapi` / `starlette`
- `app.db.*` / `psycopg2` / `redis`
- `app.config.settings`
- `app.core.auth.disclosure` (raises HTTPException)
- `app.core.auth.mfa` / `app.shared.token_blacklist` (Redis-bound)
- `boto3` / `app.shared.cloud_backends`

Routers and services should depend on these contracts when they
need stable types and pure policy. The implementation surface
(FastAPI Depends, DB lookups, Redis-bound state, anti-enumeration
disclosure) stays on `app.core.auth.auth` and the corresponding
leaf modules.

Today: only `app.shared.contracts.auth` is open. Future contract
modules (e.g. `storage`, `observability`) should follow the same
purity rule.
"""
