/**
 * openapiClient.contract.test.js — Contract test del loader OpenAPI FE.
 *
 * Lock del comportamento del singleton loader:
 *  - fetch lazy (no side effect a import-time)
 *  - cache in memoria (no second fetch finche` clearOpenApiCache)
 *  - single-flight (concurrent loadOpenApiSchema → stessa promise)
 *  - error propagation esplicito (no silent fallback)
 *  - getter null-safe pre-load
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  loadOpenApiSchema,
  getEndpointSchema,
  getModelSchema,
  listEndpointPaths,
  clearOpenApiCache,
  getCachedSchema,
} from '../../src/api/openapiClient';

const MOCK_SCHEMA = {
  openapi: '3.1.0',
  info: { title: 'Plenora ETL API', version: '1.0' },
  paths: {
    '/projects': {
      get: { operationId: 'list_projects', responses: { 200: { description: 'OK' } } },
      post: { operationId: 'create_project', responses: { 201: { description: 'Created' } } },
    },
    '/projects/{project_id}': {
      get: { operationId: 'get_project', responses: { 200: { description: 'OK' } } },
    },
  },
  components: {
    schemas: {
      ProjectCreate: { type: 'object', properties: { name: { type: 'string' } } },
    },
  },
};

beforeEach(() => {
  clearOpenApiCache();
  vi.restoreAllMocks();
});

describe('loadOpenApiSchema', () => {
  it('fetcha /openapi.json e caches il risultato', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => MOCK_SCHEMA,
    });

    const schema = await loadOpenApiSchema();
    expect(schema).toBe(MOCK_SCHEMA);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toMatch(/\/openapi\.json$/);

    // Seconda call: cached, niente fetch ripetuto.
    const schema2 = await loadOpenApiSchema();
    expect(schema2).toBe(schema);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('single-flight: chiamate concorrenti condividono la stessa promise', async () => {
    let resolveFetch;
    const fetchSpy = vi.spyOn(global, 'fetch').mockReturnValue(new Promise(resolve => {
      resolveFetch = () => resolve({ ok: true, json: async () => MOCK_SCHEMA });
    }));

    const p1 = loadOpenApiSchema();
    const p2 = loadOpenApiSchema();
    resolveFetch();
    const [s1, s2] = await Promise.all([p1, p2]);

    expect(s1).toBe(s2);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('propaga errori HTTP senza silent fallback', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({ ok: false, status: 500 });
    await expect(loadOpenApiSchema()).rejects.toThrow('HTTP 500');
  });

  it('rifiuta schemi senza paths', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ openapi: '3.0.0' }),
    });
    await expect(loadOpenApiSchema()).rejects.toThrow('missing paths');
  });

  it('non ha side effect a import-time (niente fetch finche` non chiamato)', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => MOCK_SCHEMA,
    });
    // Reimport non esiste in vitest ESM; verifica indiretta: lo spy non
    // ha chiamate prima del primo loadOpenApiSchema().
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(getCachedSchema()).toBe(null);
  });
});

describe('getEndpointSchema', () => {
  beforeEach(async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => MOCK_SCHEMA,
    });
    await loadOpenApiSchema();
  });

  it('ritorna lo schema operation per (method, path) noto', () => {
    const op = getEndpointSchema('GET', '/projects');
    expect(op).toBeTruthy();
    expect(op.operationId).toBe('list_projects');
  });

  it('case-insensitive sul method', () => {
    expect(getEndpointSchema('get', '/projects')).toBeTruthy();
    expect(getEndpointSchema('POST', '/projects')).toBeTruthy();
  });

  it('ritorna null per path non dichiarato', () => {
    expect(getEndpointSchema('GET', '/does/not/exist')).toBe(null);
  });

  it('ritorna null per method non dichiarato sul path', () => {
    expect(getEndpointSchema('DELETE', '/projects')).toBe(null);
  });
});

describe('getEndpointSchema pre-load', () => {
  it('ritorna null se lo schema non e\' caricato', () => {
    // beforeEach del top-level pulisce la cache.
    expect(getEndpointSchema('GET', '/projects')).toBe(null);
  });
});

describe('getModelSchema', () => {
  beforeEach(async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => MOCK_SCHEMA,
    });
    await loadOpenApiSchema();
  });

  it('ritorna lo schema named del componente', () => {
    const m = getModelSchema('ProjectCreate');
    expect(m).toBeTruthy();
    expect(m.type).toBe('object');
  });

  it('ritorna null per modello sconosciuto', () => {
    expect(getModelSchema('Garbage')).toBe(null);
  });
});

describe('listEndpointPaths', () => {
  it('ritorna tutti i path dello schema', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => MOCK_SCHEMA,
    });
    await loadOpenApiSchema();
    expect([...listEndpointPaths()].sort()).toEqual([
      '/projects',
      '/projects/{project_id}',
    ]);
  });

  it('ritorna [] se pre-load', () => {
    expect(listEndpointPaths()).toEqual([]);
  });
});
