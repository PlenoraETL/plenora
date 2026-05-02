# Residual Register

Status as of **2026-05-02**.

This file is the operational residual register for the project. It is shorter
than the roadmap and answers one question: what remains open before Plenora can
be considered product-grade.

## Status Legend

- **NEXT**: next practical batch when work resumes.
- **PARTIAL**: materially advanced, but a narrower residual remains.
- **LATER**: valid debt, but not urgent.
- **DROP**: accepted / intentional / not worth action.
- **CLOSED**: completed and kept only for traceability.

## Backend

| ID | Area | Status | Why it matters | Suggested next action |
|---|---|---|---|---|
| R-01 | Auth rate limiter in-memory | CLOSED | Redis-backed sliding-window limiter (`app/shared/login_rate_limiter.py`) with in-memory fallback when Redis is unavailable; single-node behaviour preserved byte-per-byte. Covered by 19 passing security tests (unit + contract). | Done. |
| R-05 | Scheduler heartbeat wall-clock tests | CLOSED | `app/shared/clock.py` adds `Clock`/`WallClock`/`FakeClock`; scheduler heartbeat tests were migrated off real `time.sleep`, reducing the heartbeat subset from ~50s to sub-second while preserving production `Event.wait` behaviour. | Commit `cf00387`. |
| STRUCT-001 | File service facade extraction | CLOSED | `file_service.py` was reduced from 1847 to ~750 LOC across go-ready seams: pure helpers (`app/shared/file_helpers.py`), dependency protocols/defaults (`app/services/file_service_deps.py`), storage dispatch (`app/services/file_storage_dispatch.py`), temporary upload preview (`app/services/file_preview_temp.py`), shared parser dispatch (`app/services/file_load_dispatch.py`), shared cleanup (`app/shared/file_cleanup.py`), DB/security-bound read orchestration (`app/services/file_source_read.py`), blob/temp materialization (`app/services/file_blob_materialization.py`), raw upload (`app/services/file_raw_upload.py`), parsed upload (`app/services/file_upload.py`) and multi-layer upload (`app/services/file_multi_layer_upload.py`). Compatibility wrappers/re-exports preserve existing callers; `FileSourceService` is now a facade over explicit leaf modules. | Done. Do not reopen for broad splitting; track remaining behavior changes as dedicated residuals. |
| MULTI-GDB-001 | Multi-layer GDB-in-ZIP upload | CLOSED | GDB-in-ZIP previously risked `IsADirectoryError` by reading bytes from an extracted `.gdb` directory. The fix preserves the original ZIP bytes as the stored blob while parsing from the extracted GDB path. Covered by `test_zip_unwrap_gdb_directory_uses_original_zip_blob`. | Done. |
| MULTI-ATOMIC-001 | Multi-layer upload atomicity | NEXT | Current behavior is best-effort: if layer N fails after earlier layers were saved, the earlier file sources remain persisted. This is a product/BE semantic decision, not a facade refactor. | Decide whether multi-layer upload should be all-or-nothing; if yes, add transaction/rollback behavior with contract tests. |
| MULTI-BLOB-001 | Multi-layer blob dedup/storage model | LATER | Multi-layer upload can persist N logical `FileSource` rows for one physical upload and may upload the same blob N times under object storage. This is inefficient and less Go-ready than separating blob object identity from logical layer metadata. | Analyze storage lifecycle/delete semantics before changing to shared blob references or dedup-by-hash. |
| STRUCT-005 | Auth module seams | PARTIAL | `auth.py` was reduced from ~1219 LOC to ~856 LOC by extracting rate limiting, password hashing, models, ownership helpers and JWT token helpers into focused modules with compatibility re-exports. Remaining auth endpoint mass is lower priority and should be handled only when touching auth routes. | Keep new auth work go-ready: thin router, explicit service/helper seams, contract tests. |
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
| FE-TEST-001 | Remaining hotspot coverage | CLOSED | Major hotspot batches now cover manager pages, flow modals, runs, result gating, transform/builders, editor guards/save-failure and high-ROI residual branches. Remaining items are low-ROI, fragile, or structural split work tracked separately. | Done. |
| FE-A11Y-001 | Accessibility audit | CLOSED | A11Y batches closed the identified mechanical gaps: accessible names for icon/search/toggle controls, custom modal dialog semantics, global keyboard-only `:focus-visible` policy, keyboard disclosure controls for LogViewer/RunsTab rows, contrast token bump, and dialog focus trap/restore for all dialogs found in the audit (`ModalWrapper`-based, ApiConnectionManager/DataSourceManager custom dialogs, `TicketCreateModal`, `EseguibileExecuteWizard`, DataSource config, Carican conflict, EseguibiliList, FileManager upload and DataSourceNode browser). ProjectList clickable cards are accepted as safe because explicit inner buttons provide the keyboard path. Low-ROI contrast edge cases remain accepted. | Done; only reopen on concrete accessibility regression or new custom modal. |
| FE-SPLIT-004 | Editor scaffold | CLOSED | `EditorShell` centralizes editor guards, loading/error/project-null branches and ReactFlow canvas wrapper for Estrai/Manipola/Arricchisci using `canvasConfig`, `canvasHandlers` and `slots`. `AccessDeniedBanner` deduplicates the shared permission banner and the Estrai null-project slot bug is fixed. | Done; reopen only for a concrete editor bug or a new editor variant. |
| FE-HYGIENE-001 | Dead code removal: AppHeader and orphan canvas hooks | CLOSED | `AppHeader.{jsx,css}` (orphan), `useNodeHandlers` (replaced by `useCanvasCore`) and `useCanvasShortcuts` (combo wrapper without consumers) removed; barrels updated. Net -450 LOC dead code. | Commit `4e8aaaf`. |
| FE-HYGIENE-002 | Dead code removal: SearchInput and orphan hooks | CLOSED | `SearchInput.{jsx,css}` and `SearchInputWithFilter` removed (pages use locally-scoped BEM markup, never the shared component); `useCanvasSaveShortcut` and `useInputColumns` removed as scaffold without consumers. Net -386 LOC. | Commit `3f65ccb`. |
| FE-HYGIENE-003 | Tiny editor/test residuals | CLOSED | Stale `useNodeHandlers` references in Manipola/Arricchisci editor headers cleaned; the ConfirmContext negative test now intercepts the jsdom `error` event so its intentional render error no longer leaks two stack traces into the test output. `slots.children` in `EditorShell` is kept because the contract test exercises it. | Commit `bb21a26`. |
| FE-PERF-001 | Bundle analysis | CLOSED | `ImdfPreviewModal` is now `React.lazy` inside a `Suspense` boundary in `ResultNodeImdfView`. Build emits a dedicated `ImdfPreviewModal-XXX.js` chunk, so `vendor-maplibre` and `vendor-deckgl` (~1.27 MB raw / ~366 kB gzip) are no longer reachable from the static graph of the three editor pages and their parsing/execution is deferred until the user opens an IMDF preview. Caveat: Vite still emits `<link rel="modulepreload">` for dynamic chunks by default; that warm prefetch is accepted (no `vite.config` change). | Commit `641b9bf`; reopen only if startup performance becomes a complaint or to revisit modulepreload behavior. |
| FE-KEEP-KIT | Public-API kit kept without current consumers | KEEP | `ConfirmInline`, `ModalFooter`, the `Loading*` kit (`LoadingSpinner`/`Dots`/`Skeleton`/`Inline`/`Wrapper`) and the `components/common/index.js` barrel have no live consumers but are part of the design-system surface; tree-shaking drops anything truly unused at build time. | Reopen only on an explicit retire-kit decision; do not delete piecemeal. |
| FE-CLN-002 | Inline styles and design tokens | LATER | Audit found 430 inline-style instances across 55 files; most are dynamic (IMDF/map/data-driven) or one-off, with no high-ROI duplicated pattern remaining after `AccessDeniedBanner` extraction. | Address opportunistically when touching `FE-SPLIT-001/006`, or run a dedicated design-system pass. |
| FE-SPLIT-001..003,005..006 | Remaining large component splits | LATER | All five large components (`ApiConnectionManager` 1238, `ParameterInput` 974, `ResponseConfigBuilder` 978, `ResultNodeStandardView` 880, `FileManager` 881 LOC) are currently functional and have green contract tests; no bug or product trigger justifies a split for LOC alone. `FE-SPLIT-004` is closed separately. | Split only on a concrete feature or bug trigger that touches the file. |

## Test FE

| ID | Area | Status | Why it matters | Suggested next action |
|---|---|---|---|---|
| FE-NEXT-1 | High-ROI utility/hook coverage | CLOSED | Covered validators, template variables, `useExecution`, graph/column utils. | Keep tests focused; avoid broad snapshot tests. |
| FE-TEST-001 remainder | Page/component coverage | CLOSED | High-ROI residuals were covered in the final FE batch (`19cc007`). Remaining branches are accepted as low-ROI or belong to FE-SPLIT/A11Y/design-token tracks. | Do not re-open unless a concrete regression or product risk appears. |
| Visual smoke | Light/dark and modal polish | LATER | CSS regressions may not be caught by unit tests. | Add manual smoke checklist or lightweight browser pass. |

## Test BE

| ID | Area | Status | Why it matters | Suggested next action |
|---|---|---|---|---|
| R-04/R-04b | Source-bound modernization | CLOSED | Most brittle `inspect.getsource` tests were replaced or classified. | Keep ADR-0020 as source of truth for remaining KEEP cases. |
| TR-002 | Scheduler eager harness | CLOSED | All 3 ADR-0020 Cat-3 cases migrated to behavior-bound (`ExecutionManager.__new__` + MagicMock pattern, no shared harness needed). | Keep ADR-0020 superseded note as reference. |
| R-05 | Scheduler heartbeat `time.sleep` tests | CLOSED | Clock seam plus `FakeClock` migrated heartbeat/lock-lost unit tests off wall-clock sleeps; scheduler/unit stays green and is ~50s faster on the affected subset. | Done. |
| Full backend suite time | Local feedback loop | NEXT policy | Backend regression can bottleneck work. | Local: targeted + one wider max. CI: full regression. |

## Recommended Sequence

1. Finish docs cleanup and freeze the current state.
2. Verify a new PC can work from Git using `docs/development/onboarding.md`.
3. Keep FE in standby. The frontend track is closed across `FE-TEST-001`, `FE-A11Y-001`, `FE-SPLIT-004`, `FE-HYGIENE-001/002/003` and `FE-PERF-001`; reopen only on a concrete bug, product need or new editor variant.
4. Run one ops batch: DR drill.
5. ~~Plan R-01 Redis rate limiter as a dedicated Phase 2 backend batch.~~ **Done** (Redis-backed limiter + in-memory fallback, 19 tests passed).
6. ~~Plan R-05 scheduler clock injection.~~ **Done** (clock seam + heartbeat tests migrated, commit `cf00387`).

## Non-Goals For The Next Batch

- Do not start structural monolith splits without a dedicated window.
- Do not run full backend tests locally by default.
- Do not move or rewrite the `docs/audits/orchestrate/` snapshot.
- Do not delete ADRs; they are append-only decisions.
