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

describe('datasources api contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('createDataSource forwards the payload unchanged', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { id: 'ds-1' } });

    const { createDataSource } = await import('../../src/api/datasources');
    const payload = {
      name: 'FTP inbound',
      ds_type: 'ftp',
      config: '{"host":"ftp.example"}',
    };
    const result = await createDataSource(payload);

    expect(httpMock.post).toHaveBeenCalledWith('/datasources', payload);
    expect(result).toEqual({ id: 'ds-1' });
  });

  it('listFiles preserves path, pattern and include_hash filters', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { files: [] } });

    const { listFiles } = await import('../../src/api/datasources');
    const result = await listFiles('ds-2', {
      path: '/drop/inbound',
      pattern: '*.csv',
      include_hash: true,
    });

    expect(httpMock.get).toHaveBeenCalledWith('/datasources/ds-2/files', {
      params: {
        path: '/drop/inbound',
        include_hash: true,
        pattern: '*.csv',
      },
    });
    expect(result).toEqual({ files: [] });
  });

  it('deleteDataSource does not force a response body contract', async () => {
    httpMock.delete.mockResolvedValueOnce({ data: undefined });

    const { deleteDataSource } = await import('../../src/api/datasources');
    await deleteDataSource('ds-3');

    expect(httpMock.delete).toHaveBeenCalledWith('/datasources/ds-3');
  });

  // ─── C3.2 Phase 4: file schema endpoint ─────────────────────────────────
  // Pre-C3.2 era una chiamata diretta http.get in DataSourceNode.jsx; ora
  // passa per il boundary api/datasources.

  it('getFileSchema calls GET /datasources/:id/files/schema with path query param', async () => {
    httpMock.get.mockResolvedValueOnce({
      data: { columns: [{ name: 'id', type: 'int' }], format: 'csv' },
    });

    const { getFileSchema } = await import('../../src/api/datasources');
    const result = await getFileSchema('ds-7', '/inbound/cities.csv');

    expect(httpMock.get).toHaveBeenCalledWith(
      '/datasources/ds-7/files/schema',
      { params: { path: '/inbound/cities.csv' } },
    );
    expect(result.columns).toHaveLength(1);
    expect(result.format).toBe('csv');
  });
});
