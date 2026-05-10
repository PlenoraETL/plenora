# ADR-0020: Source-Bound Test Keep List

**Status**: Accepted

**Date**: 2026-05-01

## Context

Most tests should verify runtime behavior. A small number of architecture
tests intentionally inspect source or AST shape because the property being
tested is structural rather than behavioral.

Without a policy, those tests are easy to misclassify as brittle leftovers.

## Decision

`inspect.getsource` and AST/source-shape tests are allowed only when they fit
one of these categories:

1. **Static policy enforcement**: the test scans code to enforce a codebase
   rule, such as audit coverage or required warning placement.
2. **AST structural sentinel**: the test verifies a structural property that
   is the contract, such as a call not being nested under a guard.
3. **Temporary integration gap**: allowed only with a named owner and a
   conversion path. No open cases should be added without review.

When a behavior-bound test can enforce the same contract with reasonable
setup, prefer the behavior-bound test.

## Current Keep Rules

- Keep source-shape tests narrow and named after the policy they enforce.
- Prefer AST parsing over substring matching when testing structure.
- Do not use source scans to pin incidental implementation order or formatting.
- Every new source-bound test must state which category it belongs to.
- Scheduler and Celery propagation behavior should be tested through harnesses
  where practical.

## Consequences

Positive:

- Legitimate architecture guards remain available.
- Reviews have an objective rule for accepting or rejecting new source scans.
- Runtime correctness tests remain the default.

Negative:

- Source-shape tests still need periodic pruning when behavior harnesses become
  cheap enough.

## References

- [Architecture Invariants](../invariants.md)
- `backend/tests2/`
