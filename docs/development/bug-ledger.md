# Backend Bug Marker Ledger

This file tracks numbered backend bug/FIXME markers that still appear in
source comments or regression tests. Keep entries small and update this ledger
before adding a new `FIXME #N`, `BUG #N`, or `BUG.md #N` marker.

## Tracked Markers

### #7 - Compatibility coverage for older canvas resource IDs

Some saved canvases still contain `urby-*` resource identifiers and need
compatibility coverage while access checks and annotations remain strict.

Current references:
- `backend/tests2/projects/test_get_project_access_denied_annotation.py`
- `backend/tests2/security/unit/test_c11_contract.py`

### #8 - Access denied annotations for invisible resources

Canvas resources that are not visible to the caller must produce clear access
denied annotations without leaking cross-tenant data.

Current references:
- `backend/tests2/projects/test_get_project_access_denied_annotation.py`
- `backend/tests2/security/unit/test_c11_contract.py`

### #11 - Date type inference contract

Date-like inputs must keep stable inferred types across project/file service
contracts.

Current references:
- `backend/tests2/services/test_type_inferrer_date_contract.py`

### #14 - Batch enrichment row explosion merge

Batch enrichment must preserve rows correctly when a node expands a result set
through FeatureCollection-style responses.

Current references:
- `backend/tests2/arricchisci/unit/test_batch_parallel_merge_contract.py`

### #15 - Cardinality explosion topology

Node cardinality analysis must identify topologies where one predecessor plus
multiple siblings can multiply downstream records.

Current references:
- `backend/tests2/arricchisci/unit/test_node_may_explode_cardinality_contract.py`

### #16 - Project visibility ValueError handling

Visibility checks for project resources must not leak raw `ValueError` paths
into security-sensitive flows.

Current references:
- `backend/tests2/security/unit/test_c11_contract.py`

### #25 - Manipola upstream fallback behavior

Manipola schema/execution fallback must avoid returning upstream state when
node transformation handling fails.

Current references:
- `backend/app/core/manipola/executor.py`

### #40 - Join schema inference key preservation

Schema inference for joins with different left/right keys must preserve the
right key name rather than collapsing it into the left key.

Current references:
- `backend/tests2/manipola/unit/test_schema_inference_join_contract.py`
