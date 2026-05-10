# ADR-0012: Preview Execute Policy

**Status**: Accepted

**Date**: 2026-04-21

## Context

Preview and full execution paths share domain logic but have different runtime
contracts. Preview must be bounded, fast enough for UI interaction, and safe
for partial data. Execute may process full datasets and write durable results.

## Decision

Preview and execute paths must follow the same domain semantics while keeping
different resource limits.

Preview:

- uses bounded row counts and payload sizes;
- returns stable preview shapes for the frontend;
- must not silently swallow classifiable errors;
- must not perform durable writes except explicitly documented temporary or
  cache state.

Execute:

- may process full datasets according to configured resource caps;
- writes intermediate state, artifacts, and results according to runtime
  policy;
- must return typed or explicitly bounded result payloads.

## API Contract

Preview responses should converge on:

```text
success
message
columns
rows
total_count
preview_count
truncated
```

Domain-specific fields are allowed only when documented by the owning module
and covered by tests.

## Consequences

Positive:

- Frontend preview handling can depend on stable fields.
- Preview cannot accidentally become a full execution path.
- Domain errors remain visible to users and operators.

Negative:

- Some modules need adapters while their native preview shapes converge.
- Shared preview abstractions should remain small until duplication is proven.

## References

- `backend/app/core/estrai/`
- `backend/app/core/manipola/`
- `backend/app/core/arricchisci/`
- [Architecture Invariants](../invariants.md)
