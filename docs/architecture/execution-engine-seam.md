# Execution Engine Seam

Status reviewed: 2026-05-07.

This page describes the boundary between the shared execution layer and the
domain-specific engines that still execute Plenora canvases.

It is a live architecture guide.

## Current Shape

The shared execution package now owns structural contracts:

- `app.shared.execution.canvas_node.CanvasNode`
- `app.shared.execution.canvas_graph.CanvasGraph`
- `app.shared.execution.context.CanvasExecutionContext`
- `app.shared.execution.graph.kahn_topological_order`
- `app.shared.execution.graph.GraphCycleError`
- `app.shared.execution.executor_protocol.CanvasExecutorProtocol`
- `app.shared.execution.executor_protocol.ExecutionResult`
- `app.shared.execution.payloads.ArtifactPayload`

Domain engines still own business execution:

- `app.core.manipola`: dataframe transforms, joins, aggregations, spatial
  operations, result persistence.
- `app.core.arricchisci`: REST/API enrichment, file-mode enrichment,
  per-row retry/rate-limit/dedup behavior.
- `app.core.estrai`: SQL/ArcGIS source extraction.
- `app.core.carica`: destination writes and publishing.
- `app.core.imdf`: IMDF bundle validation and publish orchestration.

## Boundary Rule

Shared execution code may know about:

- canvas structure;
- nodes and edges;
- graph ordering;
- run context;
- bounded result contracts.

Shared execution code must not know about:

- Pandas/GeoPandas transform semantics;
- SQL dialect generation;
- HTTP enrichment behavior;
- ArcGIS concrete clients;
- database repositories;
- object-storage clients.

## What Has Already Moved

| Capability | Current home | Notes |
|---|---|---|
| Topological sort | `app.shared.execution.graph` | Used by Manipola and Arricchisci. |
| Canvas node access | `app.shared.execution.canvas_node` | Replaces repeated dict pinching patterns. |
| Canvas graph structure | `app.shared.execution.canvas_graph` | Boundary object for graph-level access. |
| Execution context base | `app.shared.execution.context` | Arricchisci keeps a compatibility subclass. |
| Executor protocol | `app.shared.execution.executor_protocol` | Orchestrator dispatch can depend on the protocol. |
| Result contract | `ExecutionResult` | Guards payloads against DataFrames, bytes, and streams. |
| Artifact payload | `ArtifactPayload` | First typed payload for result references. |

## What Must Stay Domain-Specific

| Domain behavior | Why it stays out of shared execution |
|---|---|
| Manipola join/filter/group-by/spatial math | Transformation semantics, not graph structure. |
| Manipola OOM/cardinality guards | Specific to dataframe and join risks. |
| Arricchisci HTTP retry/rate-limit/dedup | External API behavior, not generic execution. |
| Arricchisci file processor mode | Enrichment-specific result mode. |
| EstrAi SQL generation | Dialect and source-specific data plane. |
| Carica destination writer | Persistence/write semantics. |
| IMDF validation rules | Product/domain format behavior. |

## Remaining Seams

| Seam | Current state | Direction |
|---|---|---|
| Source loading | Manipola and Arricchisci still have separate source-loading flows. | Move toward a resource-aware dispatcher only if it stays small and context-explicit. |
| Metadata travel | Manipola consumes `LoadedResource.metadata` in parts, but some executors still rebuild metadata from canvas dictionaries. | Loader should produce metadata once; executors should consume it. |
| Result return type | Some paths still convert typed results back to dictionaries. | Let orchestrator and callers consume `ExecutionResult` natively. |
| Canvas parsing | Some loops still instantiate node wrappers near use sites. | Parse once at the boundary into `CanvasGraph` where practical. |
| Payload typing | `ArtifactPayload` exists; other payloads may still be generic. | Add typed payloads only for stable recurring shapes. |

## Anti-Goals

- Do not merge Manipola and Arricchisci into a generic mega-engine just
  because both traverse graphs.
- Do not put transformation logic into `CanvasNode`.
- Do not create a `BaseGraphExecutor` before duplication is proven and
  measurable.
- Do not use shared execution as a place to hide compatibility facades.
- Do not allow `ExecutionResult.payload` to carry large data.

## Extraction Order

1. **Typed result propagation**
   Keep converting executor dictionaries at the boundary for compatibility,
   but move internal orchestrator paths toward native `ExecutionResult`.

2. **CanvasGraph intake**
   Convert raw canvas dictionaries once at execution/preview entry points
   where the call path is stable.

3. **Metadata travel**
   Finish moving source metadata production into loaders and stop rebuilding
   metadata from column dictionaries in executors.

4. **Source loading convergence**
   Consolidate only the parts that are actually common: source references,
   context checks, metadata envelopes, and bounded loading. Keep recompute and
   enrichment-specific branches in their domains.

5. **Payload typing**
   Add small typed payloads for recurring output shapes. Avoid a single
   catch-all payload object.

## Go-Ready Meaning Here

This boundary is go-ready when a future Go implementation can express the
same design as structs and interfaces:

```text
CanvasGraph
CanvasNode
CanvasExecutionContext
CanvasExecutor
ExecutionResult
ArtifactPayload
ResourceMetadata
```

No hidden context, no generic map payloads for large data, no domain behavior
inside structural graph types.

## References

- [Architecture overview](overview.md)
- [Horizontal engines](horizontal-engines.md)
- [Macro components](macro-components.md)
- [Architecture invariants](invariants.md)
- `backend/app/shared/execution/`
- `backend/app/shared/resources/metadata.py`
- `backend/app/core/orchestrator/service.py`
