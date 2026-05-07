# ADR-0024: Self-documenting runtime code policy

**Status**: Accepted
**Date**: 2026-05-07
**Deciders**: @marco, Codex

## Context

The backend cleanup removed historical batch notes, migration breadcrumbs, and
verbose comments from Python runtime modules. That made the repository easier to
scan, but it also reduced the amount of explanatory text near dynamic Python
code.

Python modules that move data through pandas, geospatial libraries, Celery, and
storage providers cannot rely on prose comments as the primary contract. After
the cleanup, readability must come from stable names, explicit types, module
boundaries, and current ADRs.

## Decision

Runtime Python code follows a self-documenting policy:

- imports live at module top by default;
- local imports are allowed only for circular dependencies, optional/heavy
  dependencies, or bootstrap/runtime boundaries;
- type hints and return annotations are expected on new or touched functions;
- weak throwaway names are treated as refactor debt in runtime modules;
- comments explain why, not historical implementation steps.

The repository enforces this by baseline guard tests instead of a big-bang
rewrite. Existing debt is allowed to remain temporarily, but new changes must
not increase it.

## Rationale

The codebase is being shaped toward horizontal, portable components. Hidden
dependencies, untyped payloads, and ambiguous names work against that goal more
than missing prose comments do. Baseline guards make the direction measurable:
the numbers can only stay flat or go down.

This is safer than requiring full typing immediately. The backend still contains
large legacy modules and dynamic data paths; forcing every function to be typed
in one change would create noisy annotations and risky churn.

## Alternatives considered

- **Restore long docstrings**: rejected. It would reintroduce the same noise
  that was removed and would not make dynamic payloads safer.
- **Full mypy adoption immediately**: rejected for now. Useful later, but too
  disruptive before the large executor/service modules are split.
- **No enforcement**: rejected. Without guardrails, local imports, weak names,
  and missing annotations would grow again during feature work.

## Consequences

- **Positive**: future refactors have objective counters for import locality,
  type hint debt, and naming debt.
- **Positive**: code review can focus on lowering baselines rather than arguing
  about style subjectively.
- **Negative**: some tests intentionally pin imperfect baselines until the
  larger modules are refactored.
- **Neutral**: this ADR does not require all private helpers to have docstrings.

## References

- Guard: `backend/tests2/shared/test_local_import_policy.py`
- Guard: `backend/tests2/shared/test_self_documenting_code_policy.py`
- Guide: `docs/development/contributing.md`
- Related: `docs/architecture/horizontal-engines.md`
