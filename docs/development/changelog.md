# Changelog

This file is a lightweight human index. Git history remains the source of
truth for exact changes.

## 2026-05-14

- **Audit cross-modulo + Sprint hardening**. Tre wave di lavoro su
  backend contratto e frontend complessita`:
  - Sprint 1: SQL safety hardening (`safe_set_clause` adopted on
    `eseguibili`), `scheduler/db.py` allowlist regression fix, dead
    code cleanup (`useCaricaSchemaSelect.loadDestColumns`),
    cancellation guard sulle fetch a catena.
  - Sprint 2 — architettura contratto FE↔BE: `X-API-Version` header +
    FE drift detector, `app/shared/canvas_types.py` centralizzato (9
    TypeAlias Literal), mirror FE `canvasTypes.js` + contract test,
    fix di 2 drift storici (`conflict_policy "update"→"force"` con
    validator legacy, `rest_method DELETE` rimosso BE↔FE allineato),
    `/openapi.json` sempre esposto (anche in prod) + fix ForwardRef
    Pydantic v2 sui router auth, `openapiClient` FE loader + contract
    cross-stack che verifica i 132 endpoint chiamati dal FE esistano
    su OpenAPI BE.
  - Sprint 2.5 (parziale): Playwright `logout.spec.js` +
    `project-crud.spec.js`. Pipeline E2E completa richiede DB seeded
    + Celery worker container — rimasta fuori scope.
  - Sprint 3 (parziale): `except Exception` budget contract per
    evitare regressioni silenti su 692 broad-catch esistenti.
- **Decomposizione 3 giganti FE** con pattern subdir omonima:
  - `CaricanNode.jsx` 1020 → 216 righe (-79%) in 7 step.
  - `ProjectList.jsx` 813 → 452 righe (-44%) in 5 step.
  - `ImdfPublisherNode.jsx` 519 → 289 righe (-44%) in 4 step.
- **Falso drift chiarito**: `dest_type='arcgis'` non e' drift FE↔BE,
  e' doppio canale by-design (Node.type='carica_arcgis' come
  discriminator UI + dest_type='arcgis' come branch executor).

Pendenze tracciate (fuori scope sessione): SSE per task status,
Pipeline E2E completa Playwright, Nginx proxy `/api` runtime,
dual-shape error envelope removal (40+ test BE + 7 consumer FE).

## 2026-05-07

- Documentation was collapsed into a smaller canonical set.
- Live install docs were rewritten around executable acceptance gates.
- Release 1.0 readiness was promoted to a product document.
- Compliance docs were shortened into current working evidence.
- Closed audit material was removed from the canonical architecture set.

## Current Development Themes

- Horizontal backend engine boundaries.
- Resource and artifact safety.
- Clean installation on a fresh Linux host.
- Docker validation, including the remaining frontend delivery gap.
- Documentation cleanup and removal of obsolete operational noise.

## Where To Look

- Product readiness: [`../product/release-1.0.md`](../product/release-1.0.md)
- Architecture: [`../architecture/overview.md`](../architecture/overview.md)
- Horizontal engines: [`../architecture/horizontal-engines.md`](../architecture/horizontal-engines.md)
- Install, non-Docker: [`../ops/install-non-docker.md`](../ops/install-non-docker.md)
- Install, Docker: [`../ops/install-docker.md`](../ops/install-docker.md)

For exact commit-by-commit detail, use:

```bash
git log --oneline --decorate --graph
```
