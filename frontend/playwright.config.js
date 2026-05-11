// Playwright smoke suite UI.
//
// Scope intenzionale: solo smoke. Verifica che le pagine principali
// montino senza crash + che il login funzioni end-to-end contro lo
// stack docker compose (frontend :3000, backend :5004).
//
// Pre-requisito: stack `up`. NESSUN webServer auto-start: usare i
// container gia' attivi (vite production build + nginx). Per il
// dev server locale basta `npm run dev` separato e cambiare baseURL.
//
// Non sostituisce gli unit/contract test in `tests/` (vitest); copre
// solo il path utente reale dove il rendering React + chiamate API
// devono cooperare.
//
// Auth model: AuthContext usa `sessionStorage`, che storageState non
// serializza. La fixture `authedPage` (e2e/fixtures.js) inietta il
// token via addInitScript per ogni test che non testa il login.

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  // Tempo per assert/locator. La login backend puo' tardare in cold
  // start, ma 10s sono ampi.
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,         // smoke: 1-by-1, evita race su DB shared
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    ignoreHTTPSErrors: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
