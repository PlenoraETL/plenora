// Smoke su canvas editor (ESTRAI / MANIPOLA / ARRICCHISCI).
//
// Per ognuno dei 3 moduli:
//  1. Crea un progetto vuoto via API (beforeAll, batch)
//  2. Naviga alla pagina /editor/... corrispondente
//  3. Verifica che il canvas ReactFlow monti (.react-flow)
//  4. Verifica nessun JS error fatale durante il render
//  5. Cleanup: cancella i progetti via API (afterAll)
//
// I 3 progetti vivono dentro l'utente admin_plenora e vengono ripuliti
// a fine run. La rete API e' parlata direttamente con fetch del
// browser context cosi' la sessione condivide gli stessi headers/CSRF
// del frontend (se mai introdotti).

import { request as pwRequest } from '@playwright/test';
import { test, expect, ADMIN_USER, ADMIN_PASS, API } from './fixtures.js';

/** @type {string} */
let bearer;
/** @type {Record<'estrai'|'manipola'|'arricchisci', string>} */
const projectIds = { estrai: '', manipola: '', arricchisci: '' };

test.beforeAll(async () => {
  const api = await pwRequest.newContext({ baseURL: API });
  const loginResp = await api.post('/api/auth/login', {
    data: { username: ADMIN_USER, password: ADMIN_PASS },
  });
  expect(loginResp.ok(), 'login admin per setup canvas').toBeTruthy();
  const { access_token } = await loginResp.json();
  bearer = access_token;
  expect(bearer).toBeTruthy();

  for (const kind of /** @type {const} */ (['estrai', 'manipola', 'arricchisci'])) {
    const stamp = `pw-smoke-${kind}-${Date.now()}`;
    const createResp = await api.post('/api/projects', {
      headers: { Authorization: `Bearer ${bearer}` },
      data: {
        name: stamp,
        description: 'auto-created by playwright smoke',
        project_type: kind,
        canvas: { nodes: [], edges: [] },
      },
    });
    expect(createResp.ok(), `create ${kind} project: ${createResp.status()} ${await createResp.text()}`).toBeTruthy();
    const body = await createResp.json();
    // ProjectCreateResponse: { metadata: { id, ... }, canvas: { ... } }
    projectIds[kind] = body?.metadata?.id || body.id || body.project_id;
    expect(projectIds[kind], `id mancante in response create ${kind}: ${JSON.stringify(body).slice(0, 200)}`).toBeTruthy();
  }
  await api.dispose();
});

test.afterAll(async () => {
  if (!bearer) return;
  const api = await pwRequest.newContext({ baseURL: API });
  for (const id of Object.values(projectIds)) {
    if (!id) continue;
    // Best-effort: non bloccare la suite se il delete fallisce.
    await api.delete(`/api/projects/${id}`, {
      headers: { Authorization: `Bearer ${bearer}` },
    }).catch(() => {});
  }
  await api.dispose();
});

/**
 * Naviga al canvas dell'id e verifica che renderizzi senza JS error.
 * @param {import('@playwright/test').Page} page
 * @param {string} path
 * @param {string} label
 */
async function assertCanvasRenders(page, path, label) {
  /** @type {string[]} */
  const errors = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text());
  });
  /** @type {string[]} */
  const fatals = [];
  page.on('pageerror', (e) => fatals.push(e.message));

  await page.goto(path, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname, `${label} bounce a login`).not.toBe('/login');

  // ReactFlow monta un .react-flow root + canvas SVG/HTML interno.
  // Selettore stabile: e' una libreria, non un nostro componente.
  await expect(page.locator('.react-flow').first()).toBeAttached({ timeout: 15_000 });

  // Filtra errori benigni della libreria (ResizeObserver loop e' un
  // warning noto che Chromium emette in dev, non rompe niente).
  const fatalConsole = errors.filter(
    (e) =>
      !/ResizeObserver|favicon/i.test(e) &&
      /TypeError|ReferenceError|SyntaxError|Cannot read|undefined is not/i.test(e),
  );
  if (fatalConsole.length) {
    throw new Error(`${label} console fatal:\n  - ${fatalConsole.join('\n  - ')}`);
  }
  if (fatals.length) {
    throw new Error(`${label} pageerror:\n  - ${fatals.join('\n  - ')}`);
  }
}

test('ESTRAI editor mounts canvas', async ({ page }) => {
  expect(projectIds.estrai).toBeTruthy();
  await assertCanvasRenders(page, `/project/${projectIds.estrai}`, 'ESTRAI');
});

test('MANIPOLA editor mounts canvas', async ({ page }) => {
  expect(projectIds.manipola).toBeTruthy();
  await assertCanvasRenders(page, `/editor/manipola/${projectIds.manipola}`, 'MANIPOLA');
});

test('ARRICCHISCI editor mounts canvas', async ({ page }) => {
  expect(projectIds.arricchisci).toBeTruthy();
  await assertCanvasRenders(page, `/arricchisci/${projectIds.arricchisci}`, 'ARRICCHISCI');
});
