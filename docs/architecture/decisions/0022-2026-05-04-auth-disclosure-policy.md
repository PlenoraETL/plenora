# ADR-0022: Auth Disclosure Policy And Domain Error Codes

**Status**: Accepted  
**Date**: 2026-05-04  
**Deciders**: @CloudIA  
**Complements**: ADR-0009, ADR-0018

## Context

Before `BE-GO-READY-AUTH-DOMAIN-PROMOTION-001`, the auth domain still had
literal `HTTPException(..., detail="<message>")` call sites. The frontend saw
generic `http_<status>` codes instead of stable auth-domain codes, so it could
not branch cleanly on token revoked, MFA required, role denied, user missing,
or revocation infrastructure unavailable.

The change must preserve user-visible behavior exactly:

- same status codes;
- same Italian messages;
- same `WWW-Authenticate` and `Retry-After` headers;
- same anti-enumeration boundaries.

## Decision

Promote 25 auth call sites in `auth.py` and 2 ownership call sites in
`ownership.py` to typed errors using:

```python
raise_typed_http(
    code=error_codes.<CONST>,
    status_code=<status>,
    message="<italian message>",
    headers=<optional headers>,
)
```

`raise_typed_http` accepts optional headers, so the 401 token-related paths keep
`WWW-Authenticate: Bearer` and the 429 rate-limit path keeps `Retry-After`
without hand-writing structured `HTTPException` payloads.

The auth family adds 27 stable domain codes:

- 401: `auth_token_missing`, `auth_token_invalid`, `auth_token_revoked`,
  `auth_user_not_found_or_disabled`, `auth_invalid_credentials`,
  `auth_mfa_invalid`.
- 403: `auth_account_disabled`, `auth_mfa_required`,
  `auth_role_insufficient_for_write`, `auth_role_kiosk_only`,
  `auth_role_admin_required`, `auth_role_superadmin_required`,
  `auth_role_create_privilege_required`, `auth_field_role_restricted`,
  `auth_self_modify_denied`, `auth_self_disable_denied`,
  `auth_hierarchical_owner_required`, `auth_owner_only`.
- 404: `user_not_found`, `group_not_found`.
- 409: `username_already_exists`, `group_id_already_exists`.
- 400: `group_default_not_deletable`, `auth_mfa_code_invalid`,
  `auth_self_delete_denied`.
- 429: `auth_rate_limit_exceeded`.
- 503: `auth_revocation_unavailable`.

## Anti-Enumeration Invariants

These invariants are source-bound in
`tests2/security/unit/test_auth_domain_codes_contract.py`:

1. Login bad credentials and unknown user return the same 401 message and the
   same code: `auth_invalid_credentials`.
2. Post-token user missing and user disabled return the same 401 message and
   the same code: `auth_user_not_found_or_disabled`.
3. Admin reset/delete for out-of-group or nonexistent targets stays uniform
   403 `access_denied`; only superadmin receives 404.
4. Account disabled remains a deliberate post-password 403 disclosure:
   `auth_account_disabled`.

## Out Of Scope

- Consolidating the role codes into a single `auth_role_required` with
  structured `details`.
- Mapping the 4 label-driven ownership 404 sites to per-resource not-found
  codes. They remain `KEEP_HTTP_404_LABEL_DRIVEN` until FE needs stable
  branching by resource type.
- Promoting local input-validation 400 messages such as password policy errors;
  these remain dynamic validation messages.

## Consequences

The frontend can branch on stable auth-specific codes without parsing
messages. The API remains behavior-preserving: status, message, headers, and
disclosure policy are unchanged.

The catalog grows by 27 domain codes and is locked by source-bound tests.

## Verification

- `tests2/shared/test_error_codes_catalog.py` locks catalog membership.
- `tests2/security/unit/test_auth_domain_codes_contract.py` locks per-site
  message/code/header preservation and anti-enumeration invariants.
- Static scan target: zero auth-domain literal string `HTTPException`
  residuals in `auth.py`; label-driven ownership 404 sites remain explicitly
  classified.
