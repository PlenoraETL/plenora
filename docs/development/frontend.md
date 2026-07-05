# Frontend Development

React 18 + Vite + ReactFlow + Zustand. The frontend is the canvas and
operations UI for Plenora: project editors, execution controls, run
history, connection management, administration pages, and task polling.

This page documents the current development contract.

## Setup

Prerequisites:

- Node.js 20+
- npm 10+
- Backend running on `http://localhost:5004` for local API calls

Commands:

```bash
cd frontend
npm install
npm run dev
npm run build
npm run preview
npx vitest run <test-file>
```

Vite runs on `http://localhost:3000` in this repo. The dev proxy forwards
API calls to the backend port configured in `vite.config.js`.

## Main Areas

The active source tree is organized by UI responsibility:

- `src/pages/`: route-level pages and editors.
- `src/components/`: nodes, modals, panels, transform controls, common UI.
- `src/hooks/`: polling, canvas state helpers, preview and execution flows.
- `src/api/`: HTTP client and endpoint wrappers.
- `src/store/`: Zustand stores.
- `src/context/`: auth, toast, confirm providers.
- `src/utils/`: small frontend-only helpers.
- `tests/`: Vitest contract and behavior tests.

The frontend should remain a consumer of backend contracts, not a second
source of business truth. If a rule belongs to tenant isolation,
authorization, resource loading, or execution, it belongs in the backend.

## API Contract

Use `src/api/http.js` for all backend calls. Endpoint modules should unwrap
Axios responses and return `data`.

Backend errors are normalized by the HTTP layer. UI code should prefer:

```js
err.message
err.code
err.details
```

Avoid new direct reads of retired backend payloads such as
`err.response.data.detail`. The few remaining exception paths must be
intentional compatibility code and covered by targeted tests.

## Execution UI

Canvas editors dispatch execution to the backend and poll task state through
the shared polling hooks. The UI must not carry large payloads in component
state. Result previews should use backend-provided references, caps, and
preview endpoints rather than attempting to materialize full artifacts in
the browser.

## Testing Policy

Use targeted Vitest runs while developing:

```bash
cd frontend
npx vitest run tests/pages/<file>.test.jsx
npx vitest run tests/components/<file>.test.jsx
npx vitest run tests/api/<file>.test.js
```

Run `npm run build` for broad UI, routing, bundling, or dependency changes.
Use screenshot/Playwright verification only for layout-sensitive work.

## Static Checks

There is no ESLint configuration in the repository today. The baseline
frontend quality gate is:

```bash
cd frontend
npm run build
npx vitest run tests/api tests/components tests/pages tests/hooks
```

For dependency reachability audits, use the app entry point
`src/index.jsx`. A file that is not reachable from that graph is not
necessarily removable if tests import it as a contract helper.

Current special case:

- `src/api/openapiClient.js` is not used by the runtime bundle, but it is a
  contract/test utility for OpenAPI drift checks and is covered by
  `tests/api/openapiClient.contract.test.js`.

## Docker Status

The frontend has a Docker/nginx delivery path in the root compose setup.
Treat it as release-ready only after the acceptance gate in
`docs/ops/install-docker.md` passes on a clean Linux host.

## References

- [Architecture Overview](../architecture/overview.md)
- [Horizontal Engines](../architecture/horizontal-engines.md)
- [Backend Development](backend.md)
