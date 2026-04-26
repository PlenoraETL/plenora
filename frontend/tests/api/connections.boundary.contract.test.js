import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Contract test — Connections / OpenAPI / DataSource FE-API boundary
 * (C3.2 Phase 4).
 *
 * Pre-C3.2 quattro componenti UI chiamavano `http.<verb>` direttamente
 * sui rispettivi endpoint, violando il contratto architetturale
 * "frontend/src/api/ unico punto HTTP" (CODE_MAP.md). C3.2 ha
 * consolidato tutte le chiamate in:
 *   - api/apiConnections.js (CRUD + test draft + test by-id + fetch openapi)
 *   - api/datasources.js (getFileSchema)
 *
 * Questo test e' una contract regression sulla SORGENTE dei 4
 * componenti: niente render React, solo regex sui token.
 *
 * Scope C3.2: ApiConnectionManager.jsx, ConnectionSelectorModal.jsx,
 * OpenAPIImportModal.jsx, DataSourceNode.jsx.
 *
 * Scope NON coperto qui (lasciato a C3.3): AuthContext.jsx,
 * ProjectList.jsx, UserManager.jsx.
 */

const __dirname = dirname(fileURLToPath(import.meta.url));

function readSrc(rel) {
  return readFileSync(resolve(__dirname, '../../src', rel), 'utf-8');
}

const FILES = {
  apiConnectionManager: readSrc('pages/ApiConnectionManager.jsx'),
  connectionSelectorModal: readSrc(
    'components/arricchisci/ConnectionSelectorModal.jsx',
  ),
  openApiImportModal: readSrc(
    'components/arricchisci/OpenAPIImportModal.jsx',
  ),
  dataSourceNode: readSrc('components/nodes/DataSourceNode.jsx'),
};

describe('connections boundary contract (C3.2)', () => {
  describe('ApiConnectionManager.jsx', () => {
    const src = FILES.apiConnectionManager;

    it('does NOT import http directly from api/http', () => {
      // C3.2: nessuna chiamata http.* residua, niente import del client http.
      expect(/from\s+['"]\.\.\/api\/http['"]/.test(src)).toBe(false);
    });

    it('does NOT call http.get|post|put|delete on /api-connections* or /datasources', () => {
      // Nessuna invocazione diretta di http.<verb>(...) nel file.
      expect(/\bhttp\.(get|post|put|delete|patch)\s*\(/.test(src)).toBe(false);
    });

    it('imports CRUD + test functions from api/apiConnections', () => {
      const importBlock = src.match(
        /import\s*\{[^}]*\}\s*from\s*['"][^'"]*\/api\/apiConnections['"]/,
      );
      expect(importBlock).not.toBeNull();
      const named = importBlock[0];
      for (const name of [
        'listApiConnections',
        'createApiConnection',
        'updateApiConnection',
        'deleteApiConnection',
        'testApiConfig',
        'testApiConnection',
      ]) {
        expect(named.includes(name)).toBe(true);
      }
    });

    it('uses createDataSource from api/datasources (not local shadow)', () => {
      const importBlock = src.match(
        /import\s*\{[^}]*\}\s*from\s*['"][^'"]*\/api\/datasources['"]/,
      );
      expect(importBlock).not.toBeNull();
      expect(importBlock[0].includes('createDataSource')).toBe(true);
    });

    it('routes draft test to testApiConfig and by-id test to testApiConnection', () => {
      // Caso reale: due semantiche di test prima collassate sotto lo stesso
      // shadow `testApiConnection` confondente. Post-C3.2 il file usa due
      // funzioni distinte con nomi mirror dei backend handler.
      expect(/\btestApiConfig\s*\(/.test(src)).toBe(true);
      expect(/\btestApiConnection\s*\(\s*[A-Za-z0-9_$.]+\s*,/.test(src)).toBe(
        true,
      );
    });
  });

  describe('ConnectionSelectorModal.jsx', () => {
    const src = FILES.connectionSelectorModal;

    it('does NOT import http directly', () => {
      expect(/from\s+['"]\.\.\/\.\.\/api\/http['"]/.test(src)).toBe(false);
    });

    it('uses listApiConnections({ connection_type }) instead of raw http.get', () => {
      expect(
        /import\s*\{[^}]*\blistApiConnections\b[^}]*\}\s*from\s*['"][^'"]*\/api\/apiConnections['"]/.test(
          src,
        ),
      ).toBe(true);
      expect(/\blistApiConnections\s*\(\s*\{[^}]*connection_type/.test(src)).toBe(
        true,
      );
      expect(/\bhttp\.(get|post)\s*\(/.test(src)).toBe(false);
    });
  });

  describe('OpenAPIImportModal.jsx', () => {
    const src = FILES.openApiImportModal;

    it('does NOT import http directly', () => {
      expect(/from\s+['"]\.\.\/\.\.\/api\/http['"]/.test(src)).toBe(false);
    });

    it('imports and uses fetchOpenApiSchema from api/apiConnections', () => {
      expect(
        /import\s*\{[^}]*\bfetchOpenApiSchema\b[^}]*\}\s*from\s*['"][^'"]*\/api\/apiConnections['"]/.test(
          src,
        ),
      ).toBe(true);
      expect(/\bfetchOpenApiSchema\s*\(/.test(src)).toBe(true);
      expect(/\bhttp\.post\s*\(\s*['"]\/api-connections\/fetch-openapi-schema['"]/.test(src)).toBe(false);
    });
  });

  describe('DataSourceNode.jsx', () => {
    const src = FILES.dataSourceNode;

    it('does NOT import http directly (only api/datasources)', () => {
      expect(/from\s+['"]\.\.\/\.\.\/api\/http['"]/.test(src)).toBe(false);
    });

    it('imports and uses getFileSchema from api/datasources', () => {
      expect(
        /import\s*\{[^}]*\bgetFileSchema\b[^}]*\}\s*from\s*['"][^'"]*\/api\/datasources['"]/.test(
          src,
        ),
      ).toBe(true);
      expect(/\bgetFileSchema\s*\(/.test(src)).toBe(true);
      expect(/\bhttp\.get\s*\(\s*['"`].*\/datasources\//.test(src)).toBe(false);
    });
  });
});
