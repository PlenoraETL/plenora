import { vi, describe, it, expect, beforeEach } from 'vitest';

const { httpMock } = vi.hoisted(() => ({
  httpMock: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));

describe('apiConnections api contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('listApiConnections without filters calls /api-connections and returns { api_connections } shape', async () => {
    httpMock.get.mockResolvedValueOnce({
      data: { api_connections: [{ id: 'api-1', name: 'Geocode' }] },
    });

    const { listApiConnections } = await import('../../src/api/apiConnections');
    const result = await listApiConnections();

    expect(httpMock.get).toHaveBeenCalledTimes(1);
    expect(httpMock.get).toHaveBeenCalledWith('/api-connections');
    expect(result).toEqual({
      api_connections: [{ id: 'api-1', name: 'Geocode' }],
    });
    expect(result).toHaveProperty('api_connections');
    expect(result).not.toHaveProperty('connections');
  });

  it('listApiConnections forwards category filter as querystring', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { api_connections: [] } });

    const { listApiConnections } = await import('../../src/api/apiConnections');
    await listApiConnections({ category: 'geocoding' });

    expect(httpMock.get).toHaveBeenCalledWith('/api-connections?category=geocoding');
  });

  it('listApiConnections forwards connection_type filter as querystring', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { api_connections: [] } });

    const { listApiConnections } = await import('../../src/api/apiConnections');
    await listApiConnections({ connection_type: 'rest_file' });

    expect(httpMock.get).toHaveBeenCalledWith('/api-connections?connection_type=rest_file');
  });

  it('listApiConnections combines both filters in querystring order', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { api_connections: [] } });

    const { listApiConnections } = await import('../../src/api/apiConnections');
    await listApiConnections({ category: 'routing', connection_type: 'openapi' });

    expect(httpMock.get).toHaveBeenCalledWith(
      '/api-connections?category=routing&connection_type=openapi',
    );
  });

  // ─── C3.2 Phase 4: CRUD + test endpoints + fetch-openapi-schema ──────────
  // Le funzioni qui sotto erano definite come shadow inline in
  // ApiConnectionManager.jsx / OpenAPIImportModal.jsx prima di C3.2.

  it('getApiConnection calls GET /api-connections/:id and returns body', async () => {
    httpMock.get.mockResolvedValueOnce({
      data: { id: 'api-7', name: 'Geocode Pro' },
    });

    const { getApiConnection } = await import('../../src/api/apiConnections');
    const result = await getApiConnection('api-7');

    expect(httpMock.get).toHaveBeenCalledWith('/api-connections/api-7');
    expect(result).toEqual({ id: 'api-7', name: 'Geocode Pro' });
  });

  it('createApiConnection forwards the payload to POST /api-connections', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { id: 'api-new' } });

    const { createApiConnection } = await import('../../src/api/apiConnections');
    const payload = { name: 'X', url: 'https://x.example', method: 'GET' };
    const result = await createApiConnection(payload);

    expect(httpMock.post).toHaveBeenCalledWith('/api-connections', payload);
    expect(result).toEqual({ id: 'api-new' });
  });

  it('updateApiConnection calls PUT /api-connections/:id with payload', async () => {
    httpMock.put.mockResolvedValueOnce({ data: { id: 'api-7', name: 'Updated' } });

    const { updateApiConnection } = await import('../../src/api/apiConnections');
    await updateApiConnection('api-7', { name: 'Updated' });

    expect(httpMock.put).toHaveBeenCalledWith('/api-connections/api-7', {
      name: 'Updated',
    });
  });

  it('deleteApiConnection calls DELETE /api-connections/:id', async () => {
    httpMock.delete.mockResolvedValueOnce({ data: { ok: true } });

    const { deleteApiConnection } = await import('../../src/api/apiConnections');
    const result = await deleteApiConnection('api-7');

    expect(httpMock.delete).toHaveBeenCalledWith('/api-connections/api-7');
    expect(result).toEqual({ ok: true });
  });

  it('testApiConfig (no id) calls POST /api-connections/test for the draft test', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { success: true, available_fields: [] },
    });

    const { testApiConfig } = await import('../../src/api/apiConnections');
    const payload = {
      url: 'https://x.example',
      method: 'GET',
      auth: { type: 'none' },
      test_params: { q: 'rome' },
    };
    const result = await testApiConfig(payload);

    expect(httpMock.post).toHaveBeenCalledWith('/api-connections/test', payload);
    expect(result.success).toBe(true);
  });

  it('testApiConnection (with id) calls POST /api-connections/:id/test', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { success: true } });

    const { testApiConnection } = await import('../../src/api/apiConnections');
    await testApiConnection('api-9', { test_params: { q: 'rome' } });

    expect(httpMock.post).toHaveBeenCalledWith('/api-connections/api-9/test', {
      test_params: { q: 'rome' },
    });
  });

  it('testApiConfig and testApiConnection hit DIFFERENT endpoints (no aliasing)', async () => {
    httpMock.post.mockResolvedValueOnce({ data: {} });
    httpMock.post.mockResolvedValueOnce({ data: {} });

    const { testApiConfig, testApiConnection } = await import(
      '../../src/api/apiConnections'
    );
    await testApiConfig({ url: 'https://x.example' });
    await testApiConnection('api-1', { test_params: {} });

    const urls = httpMock.post.mock.calls.map(([url]) => url);
    expect(urls).toEqual(['/api-connections/test', '/api-connections/api-1/test']);
  });

  it('fetchOpenApiSchema posts the URL and returns the parsed body', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { openapi: '3.0.0', info: { title: 'X' }, paths: {} },
    });

    const { fetchOpenApiSchema } = await import('../../src/api/apiConnections');
    const result = await fetchOpenApiSchema('https://api.example/openapi.json');

    expect(httpMock.post).toHaveBeenCalledWith(
      '/api-connections/fetch-openapi-schema',
      { url: 'https://api.example/openapi.json' },
    );
    expect(result.openapi).toBe('3.0.0');
  });
});
