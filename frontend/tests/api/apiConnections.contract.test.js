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
});
