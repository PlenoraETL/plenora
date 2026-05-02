# URBY-ETL Frontend

React 18 + Vite + ReactFlow + Zustand. Canvas-based ETL builder per i 5
moduli verticali (Estrai / Manipola / Arricchisci / Carica / IMDF) +
admin pages.

> Nota: il frontend e' un **repo Git separato** (`Plenora-frontend.git`)
> rispetto al backend (`plenora.git`), ma vive nella stessa working tree
> via `frontend/` directory. Le PR sul main repo URBY-ETL toccano i file
> frontend tramite il monorepo path; il deploy frontend e' gestito a
> parte (vedi `deploy.sh:deploy_frontend`).

---

## Setup dev

### Prerequisiti

- Node.js 20+ (consigliato via `nvm`).
- npm 10+ (incluso con Node).

### Installazione

```bash
cd frontend
npm install
```

### Run

```bash
# Dev server (vite, default :5173, HMR attivo)
npm run dev

# Production build (output in frontend/dist/)
npm run build

# Preview production build locale
npm run preview

# Lint
npm run lint

# Test (vitest)
npm run test                  # full vitest run
npx vitest run <file>         # mirato
```

### Env / proxy

Il dev server vite proxia `/api/*` verso il backend. Default config in
`vite.config.js` punta a `http://localhost:5004`. Cambia se backend e'
su porta diversa.

---

## Folder structure

```
frontend/
├── package.json             # deps + scripts
├── vite.config.js           # build + dev server + chunk splitting
├── index.html               # entry HTML (theme anti-flash inline script)
├── src/
│   ├── App.jsx              # router root + provider wrapping
│   ├── index.jsx            # ReactDOM mount
│   ├── pages/               # 1 page per editor + admin pages
│   │   ├── EstraiEditor.jsx
│   │   ├── ManipolaEditor.jsx
│   │   ├── ArricchisciEditor.jsx
│   │   ├── ProjectList.jsx
│   │   ├── ApiConnectionManager.jsx
│   │   ├── ConnectionManager.jsx
│   │   ├── DataSourceManager.jsx
│   │   ├── FileManager.jsx
│   │   ├── ScheduleList.jsx
│   │   ├── EseguibiliList.jsx
│   │   ├── UserManager.jsx
│   │   ├── AdminBackupsPage.jsx
│   │   ├── AdminSystemStatusPage.jsx
│   │   ├── LogViewer.jsx
│   │   └── ...
│   ├── components/
│   │   ├── nodes/           # ReactFlow node types (TableNode, ApiEnrichNode, ResultNode, ...)
│   │   ├── modals/          # PreviewModal, JoinModal, ImdfPreviewModal, ExecutionModal, ...
│   │   ├── panels/          # ApiEnrichNode, ResponseConfigBuilder, FeatureServicePanel, QueryPreview
│   │   ├── transforms/      # TransformNode, MultiJoinNode, ParameterInput
│   │   ├── eseguibili/      # EseguibileExecuteWizard
│   │   ├── arricchisci/     # OpenAPIImportModal, ConnectionSelectorModal
│   │   ├── imdf/            # BundleSlotCard
│   │   ├── common/          # LoadingOverlay, ErrorBoundary, PermissionRevokedBanner, AppSidebar (parziale)
│   │   └── shared/          # AllowedGroupsSelector
│   ├── hooks/
│   │   ├── useTaskPolling.js     # polling Celery task base (single-flight)
│   │   ├── useCanvasCore.js      # canvas state + handlers (estrai/manipola/arricchisci)
│   │   ├── useEditorData.js      # bootstrap dati editor
│   │   ├── usePreview.js         # preview modal orchestration
│   │   ├── useExecution.js       # execute task + polling
│   │   ├── useCaricarica.js      # CARICA execution single-flight per-key
│   │   ├── useColumnPropagation.js  # column propagation cross-node
│   │   ├── useToast.js           # re-export da context/ToastContext
│   │   ├── useConfirm.js         # re-export da context/ConfirmContext
│   │   └── ...
│   ├── store/
│   │   ├── useCanvasStore.js     # Zustand canvas state
│   │   └── ...
│   ├── api/
│   │   ├── http.js               # axios instance + interceptor JWT
│   │   ├── projects.js           # /api/projects/*
│   │   ├── connections.js        # /api/connections/*
│   │   ├── apiConnections.js     # /api/api-connections/*
│   │   ├── files.js              # /api/files/*
│   │   ├── tasks.js              # /api/tasks/*
│   │   ├── imdf.js               # /api/imdf/*
│   │   ├── auth.js               # /auth/*
│   │   ├── tickets.js            # /api/tickets/*
│   │   ├── eseguibili.js         # /api/eseguibili/*
│   │   ├── adminBackups.js       # /api/admin/backups/*
│   │   ├── errors.js             # ApiError standardizzato
│   │   └── ...
│   ├── context/
│   │   ├── AuthContext.jsx       # JWT + user profile + logout
│   │   ├── ToastContext.jsx      # toast notification system
│   │   └── ConfirmContext.jsx    # confirm modal provider
│   ├── utils/
│   │   ├── nodeFactories.js      # factory per createTableNode/createTransform/...
│   │   ├── permissions.js        # role helpers (isSuperadmin, isAdmin, ...)
│   │   ├── columnUtils.js        # column type icons + label
│   │   ├── canvasAccessControl.js  # access_denied node detection
│   │   ├── validateRouteParam.js   # isValidProjectId, ...
│   │   ├── validateEstraiCanvas.js / validateManipolaCanvas.js / validateArricchisciCanvas.js
│   │   └── hexCanvas.js          # background hex animation
│   └── styles/
│       └── *.css                 # CSS modules + tokens
├── tests/
│   ├── setup.js              # vitest setup (jsdom + matchMedia polyfill + console policy)
│   ├── pages/                # contract test pages
│   ├── components/           # contract test components
│   ├── hooks/                # contract test hooks (useTaskPolling, useImdfBundles, ...)
│   ├── api/                  # contract test API client + boundary
│   ├── context/              # contract test ConfirmContext, AuthContext
│   └── shared/               # contract test cross-cutting (frontendAntipattern)
└── dist/                     # build output (gitignored)
```

---

## API client conventions

### HTTP client

`src/api/http.js` — axios instance con:

- `Authorization: Bearer <token>` da `localStorage.access_token`.
- Interceptor 401 → logout + redirect login.
- Interceptor 403 → toast error.
- Base URL relativo (proxy vite in dev, stesso origin in prod).

### Endpoint per dominio

Un file `api/<dominio>.js` per ogni gruppo di endpoint. Funzioni
esportate sono `async` che ritornano `data` (axios response unwrapped).

Esempio:

```js
// src/api/projects.js
import { http } from './http';

export const listProjects = async (params) => {
  const { data } = await http.get('/api/projects', { params });
  return data;
};

export const getProject = async (projectId) => {
  const { data } = await http.get(`/api/projects/${projectId}`);
  return data;
};
```

### Error handling

`src/api/errors.js` esporta `ApiError` per error standardizzato.
Componenti usano:

```jsx
try {
  await deleteSomething(id);
  toast.success('Eliminato');
} catch (e) {
  console.error('deleteSomething:', e);
  toast.error(e?.response?.data?.detail || e.message);
}
```

---

## Test patterns

Stack: **vitest** + **@testing-library/react** + **MSW** (per fixture
HTTP) + **userEvent**.

### File location

- `tests/<dominio>/<file>.test.{js,jsx}` per ogni area.
- Naming: `<Component>.contract.test.jsx` per contract test (asserzione
  invarianti pubbliche), `<Component>.behavior.test.jsx` per behavior
  specifico.

### Pattern tipici

**Hook test** (`useImdfBundles`, `useTaskPolling`, ecc.):

```jsx
import { renderHook, act, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

vi.mock('../../src/api/imdf', () => ({ ... }));

describe('useImdfBundles — contract', () => {
  it('fetches bundles on isImdfMode=true', async () => {
    // ...
  });
});
```

**Component test** (con dependency override):

```jsx
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfirmProvider } from '../../src/context/ConfirmContext';
// ...

vi.mock('../../src/store/useCanvasStore', () => ({ ... }));

it('handles delete bundle via toast on error', async () => {
  render(<ConfirmProvider><MyComponent /></ConfirmProvider>);
  // ...
});
```

### Console policy

`tests/setup.js` installa una **console policy** che fa fallire i test
se vengono emessi `console.error` non whitelistati. Per scenari dove
l'errore e' atteso:

```js
beforeEach(() => {
  globalThis.__frontendConsolePolicy.allowError = true;
});
```

### Test policy locale

Allineata col main repo:

- **Mirato sul file modificato**: `npx vitest run tests/<area>/<file>.test.jsx`.
- **Wider 1 area**: `npx vitest run tests/components/`.
- **Full**: `npm run test` (CI lo fa, locale solo se serve).

Niente full suite locale per default.

---

## Production build

```bash
npm run build
```

Output: `frontend/dist/`. Ottimizzazioni vite:

- esbuild minify, console.log/debug strippati (esbuild `drop`).
- Source maps disabilitate in prod.
- Code splitting: `vendor-react`, `vendor-reactflow`, `vendor-deckgl`,
  `vendor-maplibre`, `vendor-leaflet` chunks.
- Asset hashing per cache invalidation.

Deploy: vedi `deploy.sh:deploy_frontend` (npm install + build + scp/rsync).

---

## Stato moduli FE post Phase 1

| Area | Stato |
|---|---|
| Editor canvas (Estrai/Manipola/Arricchisci) | LIVE — pattern unificato (useCanvasCore + useEditorData + usePreview + useExecution). |
| Result node | LIVE — split per modalita' (standard / IMDF / validation). |
| API connection manager | LIVE — wizard 6-step. |
| Schedule list + wizard | LIVE — cron builder + run history. |
| Eseguibili | LIVE — kiosk-only role pattern. |
| Admin pages (System Status / Backups / Users / Logs / Tickets) | LIVE. |
| IMDF (preview / manifest / validation / publisher / bundles) | LIVE. |
| Toast / Confirm system | LIVE — context-based (PR #28-#30). |
| Polling (`useTaskPolling`) | LIVE — single-flight (FE-22, PR #42-#45). |
| Theme toggle | parziale — duplicazione tra pages, candidata dedup (FE-CLN-001). |

---

## Residui FE noti

Vedi [`product/roadmap.md`](../product/roadmap.md#5-frontend-residui) per la
lista completa.

Highlights:

- **FE-CLN-001** — `useTheme` hook dedup (CLOSED).
- **FE-SPLIT-004** — `EditorShell` scaffold per Estrai/Manipola/Arricchisci con API `canvasConfig`/`canvasHandlers`/`slots` (CLOSED).
- **FE-TEST-001** — coverage top-5 hotspot (CLOSED).
- **FE-A11Y-001** — accessibility audit (CLOSED).
- **FE-HYGIENE-001/002/003** — sweep dead code (`AppHeader`, `useNodeHandlers`, `useCanvasShortcuts`, `SearchInput`, `useCanvasSaveShortcut`, `useInputColumns`) e tiny residuals; -836 LOC totali (CLOSED).
- **FE-PERF-001** — `ImdfPreviewModal` ora `React.lazy`; `maplibre`/`deckgl` escono dal grafo statico dei 3 editor (CLOSED).
- **FE-KEEP-KIT** — `ConfirmInline`, `ModalFooter`, `Loading*` kit e `components/common/index.js` barrel restano come public-API surface anche senza consumer attuali (KEEP).
- **FE-CLN-002** — inline styles vs design tokens (LATER, da affrontare opportunisticamente).
- **FE-SPLIT-001/002/003,005,006** — split di componenti grandi (LATER, contract test verdi, riapertura solo su trigger concreta).

FE in standby: riaprire solo su bug, esigenza prodotto o nuovo editor variant.

---

## Riferimenti

- [README root](../../README.md) — overview Plenora.
- [architecture overview](../architecture/overview.md) — boundaries + execution flow.
- [product roadmap](../product/roadmap.md) — residui FE + roadmap.
- [onboarding](onboarding.md) — setup nuovo dev.
- [contributing](contributing.md) — branch policy + commit format + test policy.
- `frontend/RESOURCE_LEAKS.md` — note storiche su resource leaks risolti (resta nel package frontend).
