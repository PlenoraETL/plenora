# REST Connector — Consolidation & Extension Plan

Status: Phase 0 in progress. Created: 2026-07-05.

Implementation plan for the `app.core.arricchisci` REST/API connector. Goal in
two moves: **(1) centralize** the duplicated HTTP execution logic into a single
core, then **(2) extend** it — new methods, body types, response handling,
pagination, and an async submit→poll→result mode — so that at the end we have a
single execution component that is open to new cases without being rewritten
each time.

This is a live planning document; update it as phases land.

## Motivation

The connector's cross-cutting concerns are solid (rate limiting with distributed
GCRA, thread-safe + distributed OAuth2 token caching, anti-SSRF gate, TLS policy
enforcement, `Retry-After` clamp, secrets encrypted at rest and masked in
output). The weakness is the **HTTP execution path itself**, which is duplicated
across at least four sites:

- `enrichment_retry_http.py:139-156` (per-row enricher)
- `enrichment_batch_mode.py:214-229` (batch)
- `api_connection_generator_enricher.py:227-234` (generator)
- `api_connection_management.py:475-484` (connection test)
- `rest_file` is a separate, POST-only path

Symptoms that prove the duplication is a real defect (not isolated bugs):

- `allow_redirects` is hard-coded `False` everywhere **except** the generator,
  which omits it and follows redirects by default — unintended divergence.
- Timeouts were hard-coded differently per path (generator 60s, test 30s,
  rest_file 600s) and ignored `options.*_timeout`.
- HTTP verbs are GET/POST-only in each path because there is no single place to
  add methods; any non-GET verb silently becomes POST.

Adding async polling + new features on top of this shape would replicate the new
logic across the enricher **and** generator paths, making the duplication worse.
So centralization is Phase 0, before any feature work.

## Target architecture

A single entry point, **not** a single mega-function. "Cover all cases" means an
orchestrator that delegates to interchangeable strategies; extending = register a
new strategy, not edit the core (open/closed).

```
HttpExecutor.execute(RequestSpec) -> NormalizedResponse
  │  applied once: SSRF gate, TLS policy, retry, rate-limit, redirects,
  │                timeout, logging/masking
  ├─ AuthStrategy      (bearer | basic | api_key | oauth2_* | arcgis
  │                     | «mTLS | sigv4 | hmac | oauth2 authorization_code»)
  ├─ BodyEncoder       (json | form-urlencoded | multipart | raw/text | «xml»)
  ├─ ResponseParser    (json | «xml | csv | binary»)
  ├─ Paginator         (offset | cursor | link | «page-number»)
  ├─ SuccessEvaluator  (status + success_when / error_path)
  └─ AsyncPoller       (overlay: submit → poll → result, wraps execute())
```

`«...»` = future strategies (Phase 3 backlog); added without touching the core.

Consumers (per-row enricher, batch, generator, connection test) become thin:
build a `RequestSpec`, call `execute`, map the result. Existing response
processing (`output_mapping` / array explosion in
`enrichment_response_processing.py`) stays, fed from `NormalizedResponse`.

## Phase 0 — Centralization (behavior-preserving, no new features)

Objective: **identical** behavior to today, single engine underneath. Ships as
its own PR so the refactor risk is isolated from feature work.

1. **Characterization tests first** — capture current behavior of the four paths
   (enricher, batch, generator, test) on real cases, so the refactor is provably
   equivalent.
2. Define `RequestSpec` + `NormalizedResponse` (dataclasses).
3. Extract `HttpExecutor.execute` reproducing today's behavior exactly: GET/POST,
   `json=` body, retry/429/backoff, rate-limit, auth via `prepare_headers`,
   SSRF gate, TLS policy.
4. Migrate the four call sites to `execute`. `rest_file` stays out for now
   (folding evaluated in Phase 3).
5. Close the drifts during migration (low-risk, made safe by the characterization
   tests): consistent `allow_redirects`, timeouts read from options.

Deliverable: same behavior, one execution core, all paths route through it.

Current implementation note: Phase 0 has started with `RequestSpec`,
`NormalizedResponse`, and `HttpExecutor`; per-row enricher, batch, generator,
and connection test now route through the shared executor. `rest_file` remains
out of scope for Phase 0. Per-row enricher and batch validate the final URL once
before entering their retry loop, then delegate execution to the shared executor
with URL validation disabled for the already-checked URL. Batch now also uses the
same TLS `verify` policy as per-row calls; this is an intentional minor behavior
alignment from the centralization.

## Phase 1 — Cross-cutting extension (Tier A), each change in one place

With the core unified, each is localized:

- **PUT / PATCH / DELETE** — method→verb dispatch map.
- **BodyEncoder** — `form-urlencoded`, `raw/text` (`body_type` on the spec).
- **`allow_redirects`** — configurable.
- **SuccessEvaluator** — `success_when` / `error_path` (application-level errors
  returned with HTTP 200).
- **Paginator page-number** — fourth strategy in the existing switch.

## Phase 2 — Async overlay (submit → poll → result)

Generic capability for the whole class of async REST services (SISTER is one
instance expressed in config, not dedicated code).

- `AsyncPoller` wraps `execute`, reused automatically by enricher **and**
  generator (one branch, not two — the direct payoff of Phase 0).
- Reuses Phase 1 `success_when` / `error_path` for `done_values` /
  `failed_values`.
- **SISTER preset** — config only, zero SISTER-specific code.

### Async config block (`async`)

Optional block on the connection config (persisted in the same JSON on
PostgreSQL). Absent → current synchronous behavior, unchanged.

```jsonc
"async": {
  "enabled": true,
  "job_id_from": "body",            // "body" | "header"
  "job_id_path": "id",              // json-path (body) or header name (e.g. "Location")
  "accepted_status": [200, 202],    // statuses meaning "job queued"
  "status_url": "{base}/jobs/{job_id}",
  "status_method": "GET",
  "status_path": "status",
  "done_values": ["completed"],
  "failed_values": ["failed", "cancelled"],
  "result_from": "status_response", // "status_response" | "result_url"
  "result_path": "result",
  "result_url": null,
  "error_path": "error",
  "poll_interval_s": 3,
  "poll_backoff": 1.0,
  "poll_max_interval_s": 30,
  "max_wait_s": 600
}
```

Config plumbing:

- `api_connection_management.py::create_api_connection` (~lines 207-217): add
  `if data.get("async"): config["async"] = data["async"]`.
- `api_connection_management.py::update_api_connection` (`updatable_fields`,
  ~line 250): add `'async'`.
- `test_api_connection`: when `async.enabled`, run submit + a single poll cycle
  (no full `max_wait`) so the user can validate config without waiting minutes.

Recommended usage: prefer the **generator** api_type for async batch APIs (one
submit → one poll → row explosion), avoiding the per-row thread-blocking of the
enricher. Async-enricher allowed but capped (`async_max_concurrency`, default 4)
and flagged "advanced / fast per-record APIs only".

## Configuration UX

As the connector grows (async, body types, new auth, `success_when`), the config
surface grows with it. Without help it becomes a module only someone who knows
the schema can fill in. Two levels: a structural principle, and concrete
accelerators on top.

### Structural principle — self-describing strategies (schema-driven config)

Each strategy (Auth, BodyEncoder, Paginator, AsyncPoller, …) **declares its own
config descriptor**: field name, type, default, required, help text. From that
descriptor you get, for free:

- **API-side validation** — configs are validated against the descriptor instead
  of being accepted and breaking at runtime.
- **UI form generation** — the frontend renders the form from the descriptor.

So "easier configuration" is not a separate feature: it is a **property** that
follows from strategies being self-describing. Register a new strategy → its
fields appear in the form automatically, touching neither the validation backend
nor the frontend. This is the direct extension of the open/closed architecture in
this plan and should be built as part of the strategy contract (Phase 0/1), not
bolted on later.

### Accelerators (high value, contained effort)

Built on top of the schema-driven model; these remove most of the real pain:

- **cURL import.** User pastes a working `curl` → parse into a `RequestSpec`
  (URL, method, headers, body, auth). Cheap, very high value: most people test an
  API with curl/Postman first. Turns "fill 15 fields" into "paste and adjust".
- **Response sampling → auto-suggested `output_mapping`.** The fiddliest part to
  hand-write. Reuse the existing `test_api_connection`: make the real call, take
  the sample JSON, propose the json-paths, auto-detecting the array to explode.
  User confirms instead of authoring paths. Eliminates the bulk of config errors.
- **Async auto-detect from the test call.** When the test returns `202` + a
  `job_id`-like field, the UI offers "looks async — enable polling?" with
  `status_url` / `status_path` pre-filled — making the Phase 2 feature
  discoverable without prior knowledge.

### Preset library (partially exists)

The connector already has `connection_type: template` and `response_preset`. Make
it first-class: a curated **preset library** (SISTER, common geocoders, …) where
the user picks a service and fills only credentials. SISTER — already "config
only" — becomes a clickable entry instead of hand-written JSON. Low cost: the
mechanism exists, it just needs populating and surfacing.

### Reusable credentials (separate auth from connection)

Today auth lives inside each connection; a service with 5 endpoints re-configures
(and re-encrypts) credentials 5 times. Extracting **credentials as a reusable
object** referenced by connections means configuring auth once per service — and
it is safer (one place to rotate a secret). Medium effort; structural
simplification.

### Not now — OpenAPI/Swagger import

If a service publishes an OpenAPI spec, endpoints/params/auth/schema could be
generated automatically. Large value but a project of its own, and many
internal/legacy APIs have no spec. Backlog it **after** cURL import, which covers
the same need at ~1/10 the effort.

### Scope note

This spans backend **and** frontend/UX, unlike the pure-connector phases. Decide
per cycle how far to push the UI side. Recommended order: self-describing
strategies (with Phase 0/1) → cURL import + response sampling (Phase 1.5) →
preset library + reusable credentials (later).

## Phase 3 — Declared backlog (extension points ready, implement on demand)

Enabled by the architecture; not in the current pass. Each is "add a strategy",
not "touch the core".

- **AuthStrategy:** mTLS / client-cert, AWS SigV4, HMAC/request signing,
  OAuth2 authorization_code (3-legged). Most-requested in practice: mTLS.
- **ResponseParser:** XML/SOAP, CSV.
- **Fold `rest_file`** into the core if worthwhile.

## Cross-cutting

- Tests: characterization (Phase 0) → parity/regression; unit per strategy
  (Phases 1-2). Regression check: connections without `async` behave
  byte-identically.
- Docs: strategy matrix + how to register a new strategy, so the next case does
  not require re-reading the whole module.

## Risks & mitigations

1. **Refactor risk (Phase 0):** mitigated by characterization tests + an isolated
   behavior-preserving PR.
2. **`rest_file` diverging from the core:** left out in v1 to limit blast radius;
   explicit folding decision in Phase 3.
3. **God-object:** avoided by construction via strategies — the reason the target
   is a single entry point *plus* strategy registries, not one function.
4. **`max_wait_s` vs task runtime / worker timeout:** `max_wait_s` must stay below
   the Plenora task timeout; validation rejects values above a configured
   threshold.

## Estimate

| Phase | Scope | Estimate |
|---|---|---|
| 0 | Core + migration + characterization tests | ~2-3 d |
| 1 | Tier A extensions | ~1-1.5 d |
| 1.5 | Configuration UX: cURL import + response sampling (self-describing strategies folded into Phase 0/1) | ~2-3 d (BE+FE) |
| 2 | Async overlay + SISTER preset | ~1.5 d |
| **Total** | centralized + extended connector | **~5-6 d** core + **~2-3 d** config UX (Phase 3 + preset library / reusable credentials as clean backlog) |
