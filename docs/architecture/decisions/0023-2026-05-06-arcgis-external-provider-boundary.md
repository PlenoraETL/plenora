# ADR-0023: ArcGIS is an external read/write provider, not an IMDF subdomain

**Status**: Accepted
**Date**: 2026-05-06
**Deciders**: @CloudIA

## Context

Two separate backend vertical modules interact with the ArcGIS REST API:

1. **`app/core/features/`** — the Features vertical. Contains `arcgis_client.py`
   (2,476 LOC), a pure REST client with token lifecycle, pagination, retry/backoff,
   Esri type mapping, and `EsriJsonConverter`. Manages ArcGIS connection credentials
   and exposes 12 REST endpoints for connection CRUD, service browsing, preview query,
   feature write, and service creation. This is the **canonical ArcGIS transport layer**.

2. **`app/core/imdf/arcgis_uploader.py` + `arcgis/` subpackage** — the IMDF publish
   adapter. Orchestrates the 5-layer IMDF publication workflow (Localita,
   OperaPrincipale, Livello, Locale, Details) with IMDF-specific lifecycle operations
   (service creation, delete/replace/append/upsert modes, OBJECTID batch resolution).
   Contains its own ArcGIS REST communication session; does not import from Features.

A third consumer, the Estrai `featureLayerNode`, reads ArcGIS feature layers as a
pipeline data source. A fourth, the Carica `carica_arcgis` node, writes pipeline
results back to ArcGIS.

The problem: without an explicit boundary decision, **ArcGIS appears to be split
between two domains** (Features and IMDF), making it ambiguous which module "owns"
the provider, and creating a risk that future work routes IMDF → Features or adds
cross-domain imports to share client code.

A concrete symptom: the `arcgis/` subpackage sits inside `app/core/imdf/`, suggesting
IMDF owns ArcGIS. It does not. IMDF is a publish consumer. ArcGIS is external
infrastructure, symmetrical in role to PostgreSQL or S3.

## Decision

**ArcGIS is classified as an external read/write provider** (datastore capability),
belonging to macro block B — Connections / External Providers
(see [`macro-components.md`](../macro-components.md) sec. 2). It is not a
subdomain of IMDF.

Specific boundary rules:

1. `app/core/features/arcgis_client.py:ArcGISClient` is the **canonical ArcGIS REST
   client**. New code that needs to communicate with ArcGIS should prefer this client
   or a shared protocol/interface derived from it.

2. `app/core/imdf/arcgis_uploader.py` and `arcgis/` subpackage are the **IMDF publish
   adapter**. They are `KEEP_CUSTOM` (see §Rationale). They must NOT import from
   `app.core.features.*` in the target state.

   **Current known violations** (deferred imports, AST-visible):
   - `arcgis_uploader.py`: imports `FeaturesService` + `create_client` from Features.
   - `validator.py`: imports `EsriTypeMapper`, `FeaturesService`, `create_client`.

   These are documented as TODO in
   `tests2/shared/test_arcgis_provider_boundary_guard.py:_KNOWN_VIOLATIONS`.
   The guard allows these two files to skip but fails on any new cross-domain
   import. Recommended fix: see `macro-components.md` sec. 2.3.

3. Estrai and Carica interact with ArcGIS as **data source / data sink** consumers,
   using the provider via connection credentials without owning the client.

4. A source-bound import guard locks rule 2 (allows known violations, rejects new):
   `backend/tests2/shared/test_arcgis_provider_boundary_guard.py`.

## Rationale

### Why ArcGIS is a provider, not a domain

ArcGIS REST API is functionally a **remote geospatial datastore** with CRUD semantics
analogous to a relational database:

| Datastore | Read | Write | Auth |
|---|---|---|---|
| PostgreSQL | SELECT | INSERT/UPDATE/DELETE | password/SSL |
| S3 | GetObject | PutObject | IAM keys |
| ArcGIS REST | query | addFeatures / applyEdits | OAuth2 token |

Like DB and S3, ArcGIS has its own auth lifecycle (token fetch/refresh/cache),
connection credentials (managed at the platform level), and client transport layer.
None of these are domain-specific to IMDF or Estrai.

### Why IMDF's arcgis/ is KEEP_CUSTOM

The IMDF publish adapter cannot be collapsed into `ArcGISClient` because:

- **Layer order is semantic**: the 5-layer publication sequence (Localita → OperaPrincipale
  → Livello → Locale → Details) is an IMDF-domain invariant, not a general write helper.
- **Service lifecycle**: IMDF manages service creation, replacement, and cleanup as a
  unit; this is specific to the IMDF publish workflow.
- **OBJECTID resolution**: batch-resolution for upsert/update modes is tightly coupled
  to IMDF layer schema (match columns vary per layer type).

The adapter is IMDF plumbing, not ArcGIS plumbing. Moving it to the Features domain
would give Features knowledge of IMDF bundle semantics, which violates the Features
boundary (ADR-0017).

### Why the existing two-codebase split is acceptable short-term

`ArcGISClient` (Features) and the IMDF arcgis adapter both speak ArcGIS REST API
independently. This means token fetch + retry logic exists in two places. This is
acceptable short-term because:

- The token lifecycle in IMDF is specific to the publish session (single connection
  credential per publish call, not a managed connection pool).
- Coupling them now would require introducing `ArcGISClient` as a dependency of IMDF,
  which risks breaking the IMDF publish isolation and test monkeypatch contracts
  (see ADR-0016).
- The better medium-term path is to extract a shared `ArcGISClientProtocol` or a
  thin transport utility that both consumers can wrap independently.

## Alternatives considered

- **Alt A — Move all ArcGIS code to `app/core/features/`**: IMDF imports
  `ArcGISClient` from Features. — *Rejected*: IMDF has domain-specific publish
  lifecycle not appropriate in the provider module. Creates an IMDF → Features
  import dependency that violates block separation. IMDF's publish test isolation
  (monkeypatch on facade, ADR-0016) would require double-patching.

- **Alt B — Move `ArcGISClient` to `app/shared/`**: shared transport available to
  all consumers. — *Partially attractive, deferred*: requires renaming `features/`
  or splitting the Features vertical. Medium risk. Recommended as follow-up (see
  `macro-components.md` sec. 2.3).

- **Alt C — Treat IMDF arcgis/ as the canonical ArcGIS owner**: Features imports from
  IMDF. — *Rejected*: architectural inversion; IMDF is a domain consumer, not a
  platform provider. IMDF bundle semantics have no place in a generic ArcGIS client.

- **Alt D (chosen) — Classify as external provider, keep modules separate, lock
  import boundary**: no code move now; boundary decision is explicit and enforced by
  guard test; follow-up code moves in `macro-components.md` sec. 2.3.

## Consequences

**Positive**:
- Explicit boundary: developers know ArcGIS is a provider, not an IMDF subdomain.
- Import guard prevents accidental IMDF → Features coupling.
- Classification table makes it clear who owns what for future refactors.
- No code move risk in this batch.

**Negative**:
- Token / retry logic remains duplicated between `ArcGISClient` and IMDF adapter.
  Accepted short-term per §Rationale.
- `EsriTypeMapper` in `features/arcgis_client.py` is needed by both Features and
  IMDF domain; without a shared module, IMDF validator accesses it indirectly via
  the Features service. This is a CUSTOM_BUT_SHOULD_BE_GENERIC residual.

**Neutral**:
- Existing IMDF monkeypatch test strategy (ADR-0016) is unaffected.
- Features vertical status (ADR-0017) is unaffected.

## References

- Source files:
  - `backend/app/core/features/arcgis_client.py` — canonical ArcGIS REST client
  - `backend/app/core/imdf/arcgis_uploader.py` — IMDF publish adapter facade
  - `backend/app/core/imdf/arcgis/` — IMDF publish adapter subpackage
  - `backend/tests2/shared/test_arcgis_provider_boundary_guard.py` — import guard
- Macro map: `docs/architecture/macro-components.md`
- Related ADR: [ADR-0016](0016-2026-04-23-imdf-boundaries.md) — IMDF module boundaries
- Related ADR: [ADR-0017](0017-2026-04-24-features-vertical-status.md) — Features vertical
- Horizontal engine: `CONNECTION_ENGINE` (per `BE_RULES.md`)
- Follow-up: `docs/architecture/macro-components.md` sec. 2.3 (recommended code moves)
