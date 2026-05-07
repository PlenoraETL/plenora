# Historical Audits

This directory contains historical audit reports, triage notes, and
decision trails.

These files are read-only evidence. They may mention old names, old paths,
old batch numbers, or old assumptions. Do not use them as current product
documentation.

## What Belongs Here

- audit snapshots;
- triage reports;
- temporary phase workspaces;
- evidence used to create ADRs or canonical docs.

## What Does Not Belong Here

- current architecture maps;
- installation runbooks;
- release readiness criteria;
- active residual lists;
- formal ADRs.

Use these locations instead:

- ADRs: [`../architecture/decisions/`](../architecture/decisions/)
- Architecture: [`../architecture/`](../architecture/)
- Operations: [`../ops/`](../ops/)
- Product: [`../product/`](../product/)

## Current Snapshot

[`orchestrate/`](orchestrate/) preserves the old Phase 0-6 audit
workspace. It is intentionally noisy because it records how decisions were
reached. Promote only still-valid conclusions into canonical docs.
