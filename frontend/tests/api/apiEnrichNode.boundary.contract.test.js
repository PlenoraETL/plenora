import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Contract test — ApiEnrichNode FE/API boundary (C3.1 Phase 4).
 *
 * ApiEnrichNode.jsx (componente UI in components/panels/) faceva upload
 * file diretto via `http.post('/files/upload', form, { headers: ... })`,
 * bypassando l'API boundary frontend/src/api/. Conseguenze pre-C3.1:
 *  - bypass del guard B3 size pre-flight (UploadTooLargeError client-side)
 *  - bypass della propagazione 413 normalizzata
 *  - violazione del contratto architetturale "frontend/src/api/ unico
 *    punto HTTP del frontend" (CODE_MAP.md §"Contratti da non rompere")
 *
 * C3.1 fix: l'upload extra-input passa per `api/files.uploadFile`,
 * eredita guard + 413 + no manual Content-Type a costo zero.
 *
 * Questo test e' una contract assertion sulla SORGENTE del componente
 * (regex-based, non rendering): se qualcuno reintroduce la chiamata
 * diretta in PR futuro, il test rompe. Niente jsdom, niente render del
 * canvas — il pattern di violazione e' visibile sui token del file.
 *
 * Lo scope C3.1 NON copre l'altra chiamata diretta in ApiEnrichNode
 * (`http.get('/api-connections/:id')` riga ~96): rimossa in C3.2 con il
 * batch connections boundary.
 */

const __dirname = dirname(fileURLToPath(import.meta.url));
const SOURCE_PATH = resolve(
  __dirname,
  '../../src/components/panels/ApiEnrichNode.jsx',
);
const SOURCE = readFileSync(SOURCE_PATH, 'utf-8');

describe('ApiEnrichNode boundary contract (C3.1)', () => {
  it('does NOT call http.post on /files/upload directly', () => {
    // Regression: la chiamata pre-C3.1 era
    //   http.post('/files/upload', form, { headers: ... })
    // Cattura sia singolo che doppio quote, con o senza spazi.
    const pattern = /http\.post\(\s*['"]\/files\/upload['"]/;
    expect(pattern.test(SOURCE)).toBe(false);
  });

  it('does NOT force Content-Type: multipart/form-data on upload', () => {
    // Regression: pre-C3.1 (e bug speculare in tickets pre-B3) settava
    // l'header manualmente, rompendo il boundary multipart e ora gestito
    // da axios. uploadFile (api/files.js) NON setta header manuali.
    const pattern = /['"]multipart\/form-data['"]/;
    expect(pattern.test(SOURCE)).toBe(false);
  });

  it('imports uploadFile from the api/files boundary module', () => {
    // L'upload deve passare per il boundary canonical. Accetta varianti
    // di import (named import singolo o multiplo, percorso relativo).
    const pattern = /import\s*\{[^}]*\buploadFile\b[^}]*\}\s*from\s*['"][^'"]*\/api\/files['"]/;
    expect(pattern.test(SOURCE)).toBe(true);
  });

  it('actually invokes uploadFile in the upload code path', () => {
    // Sanity: l'import non deve essere "morto". Cerchiamo l'invocazione.
    const pattern = /\buploadFile\s*\(/;
    expect(pattern.test(SOURCE)).toBe(true);
  });
});
