# Phase 0 Delta - Post Storage/Intermediate Batch

Date: 2026-04-28

Scope: re-analysis of Phase 0 after the latest runtime storage and
intermediate persistence work landed on `URBY-ETL/main`.

Baseline originally referenced by Phase 0: `c68e42e`.
Current head reviewed: `b926616`.

## Delta Size

Commits since the original Phase 0 baseline include:

- runtime bug fixes;
- intermediate persistence policy/writer/reader/router work;
- full artifact writer/reader;
- object storage capability and runtime bucket factory;
- result writer fail-closed docs/gate;
- cleanup/regeneration of the test environment.

Repo delta `c68e42e..b926616`:

- 98 files changed;
- about 11,947 insertions and 407 deletions;
- major touched areas: `backend/app/core/intermediate/`,
  `backend/app/shared/cloud_backends.py`, `backend/app/utils/result_store.py`,
  `backend/app/db/results_db_service.py`, object-storage tests, and CODE_MAP.

## What Changed Phase 0 Conclusions

### Storage Boundary Is No Longer A Weak Ambiguity

Before this delta, Phase 0 treated result/intermediate storage as a product
grade concern still being shaped.

After the delta:

- runtime object storage has a common capability gate;
- `STORAGE_POLICY=object_storage` fails closed for new result writes;
- `BACKUP_BUCKET` is not a runtime fallback in object-storage policy;
- readers keep `db_legacy` compatibility explicitly;
- six target projects were cleaned and regenerated with 6/6 fresh results on
  object storage and 0 db legacy.

Result: storage/result ambiguity should move from "active architecture risk" to
"operational backlog: retention, migration legacy outside target projects,
naming cleanup".

### Intermediate Persistence Became A Real Product Boundary

New/changed modules:

- `intermediate/policy.py` (~549 lines)
- `intermediate/snapshot.py` (~589 lines)
- `intermediate/artifact_writer.py` (~341 lines)
- `intermediate/artifact_reader.py`
- `intermediate/router.py` (~636 lines)

Current behavior is materially stronger:

- `metadata_only` has zero `node_rows`;
- `preview_only` is explicit partial data;
- `full_artifact` writes parquet + manifest to runtime object storage;
- `GET /runs/{run}/nodes/{node}/data` has explicit metadata/artifact semantics;
- `compare/l2` returns explicit 422/501 cases instead of pretending rows exist.

Result: Phase 1 should not treat intermediate persistence as unknown. It is now
a first-class boundary with its own follow-up list.

## New Findings

### A0 - Internal Docstring Drift In Intermediate/Storage Modules

`backend/app/core/intermediate/policy.py`,
`backend/app/core/intermediate/snapshot.py`, `backend/app/config.py` and two
storage comments still contain old Batch 1/Batch 5 language:

- `full_artifact` "riservato per Batch 5";
- `full_artifact` "rifiutato a settings-validation";
- "niente S3 reale";
- artifact bucket / format described as reservation fields rather than active
  runtime settings.

The implementation below those comments now supports `full_artifact` and
runtime object storage. This is not a behavior bug, but it is documentation
drift inside production code and should be fixed in a small comment/docstring
batch before the next deep refactor.

Confirmed scope:

- `backend/app/core/intermediate/policy.py`: module docstring, constants
  comments, artifact status comments, and user-facing settings errors that
  still say "Batch 1".
- `backend/app/core/intermediate/snapshot.py`: module docstring still says
  `full_artifact` is rejected and no real S3 path exists.
- `backend/app/config.py`: `INTERMEDIATE_FULL_ARTIFACT_FORMAT`,
  `OBJECT_STORAGE_BUCKET` / `PLENORA_ARTIFACT_BUCKET` comments still frame the
  fields as Batch 5 prep.
- `backend/app/shared/cloud_backends.py`: cosmetic reference to
  "intermediate Batch 5" should become "intermediate full_artifact".
- `backend/app/db/intermediate_schema.sql`: cosmetic comment
  "pointer artifact full (Batch 5)" should become "full_artifact mode".

False positive: `flush_snapshots` is still a sync/no-op stub and should remain
documented that way. No async queue was introduced.

Recommended classification: A - safe/local/doc-only.

**Status: CLOSED in commit `c426685`** (2026-04-28) —
`docs(intermediate): align module docstrings with full_artifact support`.
5 files changed, +63 -42, esclusivamente docstring/commenti/messaggi
testuali. Coperti tutti i target listati sopra (policy.py, snapshot.py,
config.py, cloud_backends.py, intermediate_schema.sql). Verifica grep
post-commit: zero hit residui in `backend/app/` su `Batch 5 |
riservato per Batch | rifiutato a startup | niente S3 reale |
campo di prenotazione`.

Note: 2 riferimenti "Batch 5" residui in
`backend/migrations/2026-04-28_node_meta_persistence_columns.sql:13,17`
lasciati intenzionalmente come historical artifact (file migration
gia' applicato; non si modificano migration applied).

### D7 - Deploy Environment Alignment Now More Important

The old Phase 0 deploy risk remains, but the new storage work raises its
importance:

- production-like runtime now needs `STORAGE_POLICY=object_storage`,
  `OBJECT_STORAGE_ENABLED=true`, `OBJECT_STORAGE_BUCKET` and
  `PLENORA_ARTIFACT_BUCKET`;
- systemd long-running services read `.env`, so deploy/server alignment must
  include env inspection/update, not only `git pull` and service restart.

Recommended classification: D - deploy coordination, high before production.

### C7 - Full Artifact Diff Is A Product Follow-Up

`compare/l2` correctly returns 501 for full artifact diff. This is good
semantics, not a bug.

Carry-over:

- implement L2 diff from artifact parquet only when product needs row-level
  diff for `full_artifact` runs;
- until then, L1/hash comparison is the supported product fallback.

Recommended classification: C - product/architecture follow-up.

## Phase 1 Adjustment

The previous Phase 1 starting plan remains valid, but should be prefixed by a
very small hygiene batch:

1. A0: fix internal stale comments/docstrings around intermediate
   `full_artifact` support.
2. P1-A: OAuth wrapper deprecation docstrings + one-shot warning for
   `URBY_ALLOW_LOOPBACK_OUTBOUND`.
3. P1-B: optional logger-bound test modernization.
4. P1-C: SSRF single-source design gate.

Do not open new extraction from the intermediate modules yet. They just changed
substantially and should stabilize through usage and focused review first.

## Updated Phase 0 Verdict

The original verdict still holds: the codebase is governable but not fully
industrialized.

The delta improves the system materially:

- storage/result path is cleaner;
- DB pressure from intermediate data is reduced;
- API semantics are more explicit;
- test env is cleaner and regenerated.

Residual risk shifts from "storage ambiguity" to:

- deploy/env alignment;
- retention/cleanup policy for runtime object storage;
- stale internal docs/comments;
- future full-artifact L2 diff;
- existing Phase 0 carry-overs (SSRF, test coupling, frontend mass, compat
  residue evidence).
