import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Contract tests for the ApiEnrichNode FE/API boundary.
 *
 * Pre-C3.1 ApiEnrichNode.jsx faceva DUE chiamate dirette al client http
 * fuori dal boundary api/:
 *   1. http.post('/files/upload', form, { headers: ... }) — upload
 *      extra-input file. C3.1 lo ha consolidato dietro api/files.uploadFile,
 *      ereditando il guard B3 (size pre-flight + 413 propagation).
 *   2. http.get('/api-connections/:id') — fetch dettaglio API connection.
 *      C3.4 lo ha consolidato dietro api/apiConnections.getApiConnection
 *      (gia' esportata da C3.2). Con questo, l'import http puo' essere
 *      rimosso interamente dal componente.
 *
 * Test source-level (regex su sorgente JSX letta via node:fs): niente
 * render React, nessuna dipendenza da jsdom + ReactFlow + canvas mock.
 * Una regressione che reintroduce import http o http.<verb>() rompe il
 * test al primo `npm test`.
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


  it('does NOT import http directly from api/http (C3.4)', () => {
    // C3.4 ha rimosso `import http from '../../api/http'`: l'unica
    // chiamata residua /api-connections/:id ora passa per
    // api/apiConnections.getApiConnection.
    const pattern = /import\s+http\s+from\s+['"]\.\.\/\.\.\/api\/http['"]/;
    expect(pattern.test(SOURCE)).toBe(false);
  });

  it('does NOT call http.<verb>(...) anywhere (C3.4)', () => {
    // Niente invocazione del client http residua. Se il file vuole
    // chiamare un endpoint, deve passare per il boundary api/.
    const pattern = /\bhttp\.(get|post|put|delete|patch)\s*\(/;
    expect(pattern.test(SOURCE)).toBe(false);
  });

  it('imports getApiConnection from api/apiConnections (C3.4)', () => {
    const pattern = /import\s*\{[^}]*\bgetApiConnection\b[^}]*\}\s*from\s*['"][^'"]*\/api\/apiConnections['"]/;
    expect(pattern.test(SOURCE)).toBe(true);
  });

  it('actually invokes getApiConnection in the loadApiDetails path (C3.4)', () => {
    const pattern = /\bgetApiConnection\s*\(/;
    expect(pattern.test(SOURCE)).toBe(true);
  });
});
