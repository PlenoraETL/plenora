# Playwright smoke suite

Smoke UI test contro lo stack `docker compose` locale.

## Cosa copre

- **`login.spec.js`** — happy path login + error path (password sbagliata)
- **`lists.spec.js`** — 10 pagine "list" principali (projects, schedules, datasources, connections, files, logs, eseguibili, tickets, admin users)
- **`canvas.spec.js`** — editor canvas ESTRAI / MANIPOLA / ARRICCHISCI (ReactFlow mount)

Verifica per ogni pagina: zero JS error fatali, zero pageerror, root container montato, niente bounce inatteso a `/login`.

## Prerequisiti

Stack up: `docker compose up -d` (frontend `:3000` + backend `:5004`).
Browser: `npx playwright install chromium` (una sola volta).

Credenziali default: `admin_plenora` / `admin_plenora_2026` (utente seed locale). Override via env: `PLENORA_TEST_USER`, `PLENORA_TEST_PASS`.

## Run

```bash
npm run test:e2e          # headless, reporter "list"
npm run test:e2e:ui       # UI mode (debugger interattivo)
npm run test:e2e:report   # apri report HTML (dopo run con CI=1)
```

## Auth model

`AuthContext` usa `sessionStorage`, che Playwright `storageState` non serializza (è tab-scoped). La fixture `authedPage` (`e2e/fixtures.js`) chiama `/api/auth/login` una volta per worker e inietta token + user via `page.addInitScript` prima della prima `goto`. Tutti gli spec importano `test` da `./fixtures.js` invece che da `@playwright/test`, eccetto `login.spec.js` che testa proprio il flusso di autenticazione.

## Cleanup

`canvas.spec.js` crea 3 progetti monouso (uno per modulo) in `beforeAll` e li cancella in `afterAll`. Se la suite va in crash a metà i progetti restano: ripulibili da `Admin → Projects` o via API.
