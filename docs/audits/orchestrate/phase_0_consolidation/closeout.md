# Phase 0 - Closeout

Date: 2026-04-28

Status: closed as read-only consolidation, with post-storage delta addendum.

Read together with `delta_post_storage_2026_04_28.md`, which re-evaluates this
closeout after the runtime object-storage/intermediate batches landed.

## Scope Completed

- Baseline inventory captured in `report.md`, `baseline.md` and
  `consolidated.md`.
- Deploy/server probe documented in `probe_0_2_deploy.md`.
- Security/env boundary audit documented in `phase_0_3_security_env.md`.
- Test coupling audit documented in `phase_0_4_test_coupling_evidence.md`.
- Ledger updated with Phase 0 additions and closeout decision.
- Phase 1 brief updated with the first stabilization stream.

## Important Context After Closeout

The runtime object storage batch was completed after the first Phase 0
artifacts:

- result writer fail-closed in `STORAGE_POLICY=object_storage`;
- runtime bucket config documented and pushed;
- test environment cleanup + regeneration completed for the six target
  projects;
- regenerated results are 6/6 object storage, 0 db legacy.

This does not change the Phase 0 verdict. It strengthens the product-grade
storage boundary and reduces one operational ambiguity.

### Post-Storage Doc Closure (2026-04-28)

Following the storage/intermediate batches and the
`delta_post_storage_2026_04_28.md` re-evaluation, the new finding A0
(internal docstring drift around `full_artifact`) was closed by:

- **Commit `c426685` — `docs(intermediate): align module docstrings with full_artifact support`**:
  doc-only patch on 5 files (`policy.py`, `snapshot.py`, `config.py`,
  `cloud_backends.py`, `intermediate_schema.sql`); +63 -42; zero behavior
  change; grep verified clean of stale "Batch 5 | riservato per Batch |
  rifiutato a startup | niente S3 reale | campo di prenotazione" markers
  in `backend/app/`.

Other Phase 0 fix-now items (A2 OAuth deprecated docstrings, A3 URBY
loopback alias warning) were already closed in previous Phase 0.3
batches and re-verified in 2026-04-28 sweep — see `consolidated.md
§10.A`.

Carry-over residuals (A1, A4) remain as E-class (needs evidence
collection from production logs / DB queries), not Phase 0 code work.
R0 (git ignore warning) remains a local environment item.

**Phase 0 fully closed as of 2026-04-28 post-A0 doc closure.**

## Phase 1 Starting Point

Do not start with broad extraction.

Recommended first stabilization stream:

1. Small legacy markers:
   - deprecate OAuth compatibility wrappers in docstrings;
   - add one-shot warning for `URBY_ALLOW_LOOPBACK_OUTBOUND`.
2. Optional test-only modernization:
   - the four logger-bound test files from `phase_0_4_test_coupling_evidence.md`.
3. Design-only follow-through:
   - SSRF single-source primitive proposal, no behavior change until tests are
     modernized around the target.

## Constraints Carried Forward

- No removal of `[COMPAT-RESIDUE]` paths without log evidence.
- No mass modernization of `inspect.getsource` tests.
- No frontend/backend large-file extraction until a dedicated Phase 5-style
  structural brief exists.
- No deploy script rewrite without an explicit deploy-flow decision.
