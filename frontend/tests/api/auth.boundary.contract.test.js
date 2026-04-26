import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Contract test — Auth boundary (C3.3 Phase 4).
 *
 * Pre-C3.3 due file fuori da frontend/src/api/ avevano usage diretto del
 * client http o di fetch() per il flow di autenticazione:
 *   - context/AuthContext.jsx: http.post(/auth/login), http.post(/auth/logout),
 *     fetch() helper authFetch (export "morto", nessun consumer in src/).
 *   - pages/UserManager.jsx: import http morto (nessuna invocazione).
 *
 * C3.3 ha consolidato login/logout in api/auth.js, rimosso il helper
 * authFetch dalla Context surface, e ripulito il dead import di UserManager.
 *
 * Test source-level: niente render React, regex sui token. Coerente col
 * pattern di C3.1/C3.2 (apiEnrichNode.boundary, connections.boundary).
 *
 * Scope NON coperto qui (deliberato, lasciato a C3.4):
 *   - pages/ProjectList.jsx ha ancora `import http` e 2 chiamate dirette
 *     `/nl2pipeline*`. Decisione: la feature NL2Pipeline e' classificata
 *     come superficie morta/stale (vedi triage Phase 4). Verra' rimossa
 *     interamente in C3.4 (UI + state + polling + import + stub
 *     api/nl2pipelinePresets.js + CSS legacy + voci CODE_MAP), non
 *     migrata a un nuovo boundary api/.
 *   - components/panels/ApiEnrichNode.jsx ha ancora `import http` per
 *     un'unica chiamata residua a `/api-connections/:id` (riga ~96).
 *     Da chiudere insieme a C3.4 come "ultimo residual boundary".
 */

const __dirname = dirname(fileURLToPath(import.meta.url));

function readSrc(rel) {
  return readFileSync(resolve(__dirname, '../../src', rel), 'utf-8');
}

const FILES = {
  authContext: readSrc('context/AuthContext.jsx'),
  userManager: readSrc('pages/UserManager.jsx'),
};

describe('auth boundary contract (C3.3)', () => {
  describe('AuthContext.jsx', () => {
    const src = FILES.authContext;

    it('does NOT import http directly from api/http', () => {
      expect(/import\s+http\s+from\s+['"]\.\.\/api\/http['"]/.test(src)).toBe(
        false,
      );
    });

    it('does NOT call http.<verb> anywhere', () => {
      expect(/\bhttp\.(get|post|put|delete|patch)\s*\(/.test(src)).toBe(false);
    });

    it('does NOT call fetch() (helper authFetch removed in C3.3)', () => {
      // Esclude le occorrenze in commento cercando fetch() come
      // INVOCAZIONE: pattern "await fetch(" o "= fetch(" o "(fetch(".
      // Il commento JSDoc cita "`fetch()`" ma non ne contiene un'invocazione.
      expect(/\b(await|return|=|\()\s*fetch\s*\(/.test(src)).toBe(false);
    });

    it('imports login + logout from api/auth (with aliasing allowed)', () => {
      // Accept both `import { login, logout } from '../api/auth'` and
      // alias forms like `import { login as apiLogin } from '../api/auth'`.
      const m = src.match(/import\s*\{[^}]*\}\s*from\s*['"][^'"]*\/api\/auth['"]/);
      expect(m).not.toBeNull();
      expect(/\blogin(\s+as\s+\w+)?\b/.test(m[0])).toBe(true);
      expect(/\blogout(\s+as\s+\w+)?\b/.test(m[0])).toBe(true);
    });

    it('does NOT expose authFetch in the Context value (cleanup)', () => {
      // Pre-C3.3 il provider faceva
      //   <AuthContext.Provider value={{ ..., authFetch }}>.
      // Post-C3.3 authFetch e' rimosso dal value (no consumer).
      expect(/AuthContext\.Provider\s+value=\{[^}]*\bauthFetch\b/.test(src)).toBe(
        false,
      );
    });
  });

  describe('UserManager.jsx', () => {
    const src = FILES.userManager;

    it('removed the dead http import (was never invoked)', () => {
      expect(/import\s+http\s+from\s+['"]\.\.\/api\/http['"]/.test(src)).toBe(
        false,
      );
    });

    it('does NOT call http.<verb> anywhere', () => {
      // Sanity post-C3.3: confermiamo che l'import era davvero morto.
      expect(/\bhttp\.(get|post|put|delete|patch)\s*\(/.test(src)).toBe(false);
    });
  });
});
