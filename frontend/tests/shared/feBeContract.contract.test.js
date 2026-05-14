/**
 * feBeContract.contract.test.js — Drift detect endpoint FE↔BE.
 *
 * Parsea tutti i client API FE (`frontend/src/api/*.js`) per estrarre
 * gli endpoint chiamati via `http.<method>('/path/...')`, e per ognuno
 * verifica che esista nello snapshot OpenAPI BE
 * (`tests/fixtures/openapi.snapshot.json`).
 *
 * Cattura:
 *  - typo nel path FE (es. `/projets` invece di `/projects`)
 *  - endpoint rimossi BE-side senza aggiornare il FE
 *  - endpoint nuovi FE-side senza dichiararli sul BE
 *
 * Limitazioni esplicite:
 *  - Solo `http.get/post/put/patch/delete` (axios style). `fetch()`
 *    diretto non e' coperto — il pattern progetto e' http.* via axios.
 *  - Template literals con interpolazione all'inizio del path (es.
 *    `http.get(\\`${baseUrl}/foo\\`)`) sono skippati: il path letterale
 *    non e' deducibile staticamente.
 *  - Confronto per PREFIX path letterale: `http.get(\\`/projects/${id}\\`)`
 *    matcha qualsiasi fixture path che inizia con `/api/projects/`.
 *    Drift fine-grained (parametro rinominato, response shape) NON
 *    coperti — quelli richiedono uno strumento piu` pesante
 *    (snapshot-test per operation o validator runtime AJV).
 */

import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync, readdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const API_DIR = join(__dirname, '../../src/api');
const SNAPSHOT_PATH = join(__dirname, '../fixtures/openapi.snapshot.json');

// Endpoint chiamati dal FE che NON sono dichiarati su OpenAPI BE
// (allowlist motivate). Aggiungi qui solo dopo aver verificato che
// l'asimmetria e' intenzionale.
const ALLOWED_NON_OPENAPI_PATHS = new Set([
  // Aggiungi qui solo eccezioni motivate.
]);

// Path BE che il FE chiama tramite `${baseUrl}/...` o altri pattern
// non staticamente parsable. Skip esplicito.
const SKIP_FILES = new Set([
  'openapiClient.js',  // chiama /openapi.json (relativo a API_BASE_URL, non /api)
]);

/**
 * Strip dei commenti JS dal source per evitare false positive da
 * esempi in JSDoc (es. `* const res = await http.get('/my-endpoint');`).
 * Approccio greedy ma sufficiente per questo lock: blocchi `slash-star`
 * e line-comments. Non gestisce stringhe con `//` dentro (caso raro nei
 * client API in cui i path sono sempre letterali).
 */
function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')      // /* block */
    .replace(/^\s*\/\/.*$/gm, '');         // // line
}

/**
 * Estrae i path da una stringa di codice JS.
 * Match: http.<method>('<path>') o http.<method>(`<path>${...}`).
 * Ritorna il path letterale fino al primo `${` (template var).
 */
function extractEndpoints(source) {
  const endpoints = [];
  const cleaned = stripComments(source);
  const re = /http\.(get|post|put|patch|delete)\s*\(\s*([`'"])([^`'"$]*)/g;
  let match;
  while ((match = re.exec(cleaned)) !== null) {
    const [, method, , pathLiteral] = match;
    if (!pathLiteral.startsWith('/')) continue; // skip baseURL-relative
    endpoints.push({ method: method.toUpperCase(), path: pathLiteral });
  }
  return endpoints;
}

/**
 * Verifica se un path FE estratto (relativo, senza /api prefix)
 * matcha un path fixture OpenAPI (con /api prefix + eventuali
 * placeholder {var}).
 */
function pathMatchesAny(fePath, openapiPaths) {
  // FE chiama relativo a baseURL=http://host/api, OpenAPI ha /api/...
  const fullPath = `/api${fePath}`;

  // Match esatto.
  if (openapiPaths.has(fullPath)) return true;

  // Match prefix: il FE estratto e' "fino al primo ${"; cerca un
  // path OpenAPI che inizia con quel prefisso letterale.
  for (const oapiPath of openapiPaths) {
    if (oapiPath.startsWith(fullPath)) return true;
    // Tolleranza per trailing slash differenze.
    if (oapiPath.startsWith(fullPath + '/')) return true;
    if (fullPath.endsWith('/') && oapiPath.startsWith(fullPath.slice(0, -1))) return true;
  }

  return false;
}

describe('FE↔BE endpoint contract', () => {
  let snapshot;
  let openapiPaths;

  beforeAll(() => {
    snapshot = JSON.parse(readFileSync(SNAPSHOT_PATH, 'utf-8'));
    openapiPaths = new Set(Object.keys(snapshot.paths || {}));
  });

  it('snapshot OpenAPI ha almeno 50 paths (sanity)', () => {
    expect(openapiPaths.size).toBeGreaterThan(50);
  });

  it('ogni endpoint chiamato dai client FE esiste nello snapshot OpenAPI BE', () => {
    const apiFiles = readdirSync(API_DIR)
      .filter(f => f.endsWith('.js') && !SKIP_FILES.has(f));

    const missing = [];

    for (const file of apiFiles) {
      const source = readFileSync(join(API_DIR, file), 'utf-8');
      const endpoints = extractEndpoints(source);
      for (const { method, path } of endpoints) {
        if (ALLOWED_NON_OPENAPI_PATHS.has(path)) continue;
        if (!pathMatchesAny(path, openapiPaths)) {
          missing.push({ file, method, path });
        }
      }
    }

    if (missing.length > 0) {
      const lines = missing.map(m => `  ${m.file}: ${m.method} ${m.path}`);
      throw new Error(
        'Endpoint chiamati dal FE ma non dichiarati su OpenAPI BE:\n' +
        lines.join('\n') +
        '\nPossibili cause:\n' +
        '  (a) typo nel path FE\n' +
        '  (b) endpoint rimosso BE senza aggiornare FE\n' +
        '  (c) endpoint legittimo non in OpenAPI → aggiungi a ALLOWED_NON_OPENAPI_PATHS\n' +
        '  (d) snapshot OpenAPI obsoleto → rigenera con `UPDATE_OPENAPI_SNAPSHOT=1 pytest tests2/contract/test_openapi_snapshot.py`'
      );
    }
  });

  it('estrae correttamente endpoint da un client di prova', () => {
    const sample = `
      import http from './http';
      export const x = () => http.get('/projects');
      export const y = (id) => http.delete(\`/projects/\${id}\`);
      export const z = (id) => http.post(\`/projects/\${id}/copy\`, {});
    `;
    const eps = extractEndpoints(sample);
    expect(eps).toEqual([
      { method: 'GET', path: '/projects' },
      { method: 'DELETE', path: '/projects/' },
      { method: 'POST', path: '/projects/' },
    ]);
  });
});
