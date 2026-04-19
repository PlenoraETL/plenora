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

describe('features api contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('queryLayer serializes preview params on the request query string', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { rows: [] } });

    const { queryLayer } = await import('../../src/api/features');
    const result = await queryLayer('feat-1', 'https://example/layer/0', {
      where: 'status = 1',
      outFields: 'id,name',
      maxFeatures: 250,
    });

    expect(httpMock.post).toHaveBeenCalledWith('/features/query/preview', null, {
      params: {
        connection_id: 'feat-1',
        layer_url: 'https://example/layer/0',
        where: 'status = 1',
        out_fields: 'id,name',
        max_features: 250,
      },
    });
    expect(result).toEqual({ rows: [] });
  });

  it('writeFeatures preserves project/file mutually optional payload fields', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 'task-1' } });

    const { writeFeatures } = await import('../../src/api/features');
    const result = await writeFeatures({
      connectionId: 'feat-2',
      layerUrl: 'https://example/layer/1',
      operation: 'upsert',
      inputPath: '/tmp/export.csv',
      projectId: '',
      geometryColumn: 'geom',
      sourceSrid: 4326,
      objectIds: [1, 2],
      where: 'status = 1',
    });

    expect(httpMock.post).toHaveBeenCalledWith('/features/write', {
      connection_id: 'feat-2',
      layer_url: 'https://example/layer/1',
      operation: 'upsert',
      input_path: '/tmp/export.csv',
      project_id: '',
      geometry_column: 'geom',
      source_srid: 4326,
      object_ids: [1, 2],
      where: 'status = 1',
    });
    expect(result).toEqual({ task_id: 'task-1' });
  });

  it('createFeatureService preserves defaults for layer name and geometry column', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { service_id: 'svc-1' } });

    const { createFeatureService } = await import('../../src/api/features');
    const result = await createFeatureService({
      connectionId: 'feat-3',
      serviceName: 'published-buildings',
      inputPath: '',
      projectId: 'proj-7',
    });

    expect(httpMock.post).toHaveBeenCalledWith('/features/create-service', {
      connection_id: 'feat-3',
      service_name: 'published-buildings',
      layer_name: 'Layer 0',
      input_path: '',
      project_id: 'proj-7',
      geometry_column: 'geometry',
      source_srid: null,
    });
    expect(result).toEqual({ service_id: 'svc-1' });
  });
});
