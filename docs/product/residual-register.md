# Residual Register

Status as of **2026-05-01**.

This file is the operational residual register for the project. It is shorter
than the roadmap and answers one question: what remains open before Plenora can
be considered product-grade.

## Status Legend

- **NEXT**: next practical batch when work resumes.
- **LATER**: valid debt, but not urgent.
- **DROP**: accepted / intentional / not worth action.
- **CLOSED**: completed and kept only for traceability.

## Backend

| ID | Area | Status | Why it matters | Suggested next action |
|---|---|---|---|---|
| R-01 | Auth rate limiter in-memory | CLOSED | Redis-backed sliding-window limiter (`app/shared/login_rate_limiter.py`) with in-memory fallback when Redis is unavailable; single-node behaviour preserved byte-per-byte. Covered by 19 passing security tests (unit + contract). | Done. |
| R-30 | DB snapshot pre-deploy | NEXT | A deploy just before the daily backup can leave a long recovery window. | Add pre-deploy snapshot after staging rehearsal. |
| R-33 | DR drill not executed | NEXT manual | Recovery playbook exists but has not been proven end-to-end. | Run one documented SSH drill. |
| OBSERV-001 | Observability sink deferred | NEXT | Production support needs a clear metrics/log sink. | Decide and implement Metabase or alternative. |
| CR-001 | Compat residue SQL cleanup | NEXT manual | Legacy compatibility residue remains query-driven. | Run the documented SQL checks post-launch. |
| R-02 | bcrypt + Argon2 migration timeline | LATER | Functional today, but policy is not formalized. | Document migration policy before forcing changes. |
| R-28/R-29/R-35 | SBOM/SAST/frontend SBOM automation | LATER | Compliance automation is missing. | Add CI jobs after false-positive baseline. |
| R-19/R-20 | Large ArcGIS and enrichment monoliths | LATER | Harder to modify safely, but not current ship-blockers. | Split in Phase 5-7 with multi-PR plan. |

## Frontend

| ID | Area | Status | Why it matters | Suggested next action |
|---|---|---|---|---|
| FE-CLN-001 | ThemeToggle / useTheme dedup | CLOSED | Shared `useTheme` hook + `ThemeToggle` component extracted across pages/editors. | Commit `d309e0d`. |
| FE-TEST-001 | Remaining hotspot coverage | NEXT | Large pages/components still have limited regression guard. | Add focused tests for top remaining hotspots. |
| FE-A11Y-001 | Accessibility audit | LATER | Focus, ARIA, keyboard and contrast have not had a full pass. | Run an axe/manual audit batch. |
| FE-CLN-002 | Inline styles and design tokens | LATER | UI works, but design-system consistency is incomplete. | Move repeated inline patterns into CSS tokens. |
| FE-SPLIT-001..006 | Large component splits | LATER | Maintainability issue, not a safe pre-launch rush. | Split after product stabilization. |
| FE-PERF-001 | Bundle analysis | LATER | Geo chunks are large but already split. | Analyze only if startup performance becomes a complaint. |

## Test FE

| ID | Area | Status | Why it matters | Suggested next action |
|---|---|---|---|---|
| FE-NEXT-1 | High-ROI utility/hook coverage | CLOSED | Covered validators, template variables, `useExecution`, graph/column utils. | Keep tests focused; avoid broad snapshot tests. |
| FE-TEST-001 remainder | Page/component coverage | NEXT | FE tests are fast, so targeted additions are cheap. | Add tests by product flow, not by raw coverage percentage. |
| Visual smoke | Light/dark and modal polish | LATER | CSS regressions may not be caught by unit tests. | Add manual smoke checklist or lightweight browser pass. |

## Test BE

| ID | Area | Status | Why it matters | Suggested next action |
|---|---|---|---|---|
| R-04/R-04b | Source-bound modernization | CLOSED | Most brittle `inspect.getsource` tests were replaced or classified. | Keep ADR-0020 as source of truth for remaining KEEP cases. |
| TR-002 | Scheduler eager harness | CLOSED | All 3 ADR-0020 Cat-3 cases migrated to behavior-bound (`ExecutionManager.__new__` + MagicMock pattern, no shared harness needed). | Keep ADR-0020 superseded note as reference. |
| R-05 | Scheduler heartbeat `time.sleep` tests | LATER | Not a real flake today, but slow/thread-based. | Add clock injection only when touching scheduler runtime. |
| Full backend suite time | Local feedback loop | NEXT policy | Backend regression can bottleneck work. | Local: targeted + one wider max. CI: full regression. |

## Recommended Sequence

1. Finish docs cleanup and freeze the current state.
2. Verify a new PC can work from Git using `docs/development/onboarding.md`.
3. Run one FE batch: hotspot tests on top remaining pages/components (FE-TEST-001 remainder).
4. Run one ops batch: DR drill.
5. ~~Plan R-01 Redis rate limiter as a dedicated Phase 2 backend batch.~~ **Done** (Redis-backed limiter + in-memory fallback, 19 tests passed).

## Non-Goals For The Next Batch

- Do not start structural monolith splits without a dedicated window.
- Do not run full backend tests locally by default.
- Do not move or rewrite the `docs/audits/orchestrate/` snapshot.
- Do not delete ADRs; they are append-only decisions.
