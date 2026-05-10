# Backend Bug Marker Ledger

This file tracks numbered backend bug/FIXME markers that still appear in
source comments or regression tests. Keep entries small and update this ledger
before adding a new `FIXME #N`, `BUG #N`, or `BUG.md #N` marker.

## Tracked Markers

### #1 - Connection credential unavailable handling

Credential lookup/decrypt failures should surface as explicit client errors
instead of propagating `None` or ambiguous service failures.

Current references:
- `backend/app/core/connections/router.py`
- `backend/app/core/connections/service.py`

### #2 - Corrupted encrypted datasource/API credentials

Encrypted auth fields that cannot be decrypted must fail predictably with a
strict credential policy and a clear API response.

Current references:
- `backend/app/core/arricchisci/api_connection_management.py`
- `backend/app/core/arricchisci/router.py`
- `backend/app/core/datasources/router.py`
- `backend/app/core/datasources/service.py`
- `backend/tests2/datasources/test_service_crypto_contract.py`

### #4 - Admin backup runtime dependency degradation

Admin backup routes must degrade to a 503-style response when optional backup
runtime dependencies are unavailable.

Current references:
- `backend/app/core/admin_backups/router.py`

### #6 - Duplicate SQL result column names

SQL joins can produce duplicate column names; preview and result payloads must
deduplicate before building column metadata or record dictionaries.

Current references:
- `backend/app/core/estrai/executor.py`
- `backend/tests2/estrai/unit/test_preview_dedup_columns_contract.py`

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

### #23 - JSON non-finite number compliance

Intermediate API responses must handle `NaN`, `+Inf`, and `-Inf` because they
are not valid JSON values.

Current references:
- `backend/app/core/intermediate/router.py`

### #24 - Intermediate fingerprint mode policy

Intermediate snapshot fingerprinting has a global/sample mode transition and
sample-row policy that must stay explicit until fully settled.

Current references:
- `backend/app/config.py`
- `backend/app/core/intermediate/policy.py`
- `backend/tests2/estrai/unit/test_estrai_snapshot_routing_contract.py`
- `backend/tests2/intermediate/unit/test_snapshot_policy_contract.py`

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
