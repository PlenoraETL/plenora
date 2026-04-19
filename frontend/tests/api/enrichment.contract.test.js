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

describe('enrichment api contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('validateEnrichment forwards file source and static columns explicitly', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { valid: true } });

    const { validateEnrichment } = await import('../../src/api/enrichment');
    const result = await validateEnrichment(
      'proj-1',
      'api-1',
      { city: 'city_name' },
      'file-7',
      [{ name: 'country', value: 'IT' }],
    );

    expect(httpMock.post).toHaveBeenCalledWith('/enrichment/validate', {
      project_id: 'proj-1',
      api_id: 'api-1',
      param_mapping: { city: 'city_name' },
      file_source_id: 'file-7',
      static_columns: [{ name: 'country', value: 'IT' }],
    });
    expect(result).toEqual({ valid: true });
  });

  it('estimateEnrichment preserves a null file source when omitted', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { estimated_api_calls: 10 } });

    const { estimateEnrichment } = await import('../../src/api/enrichment');
    const result = await estimateEnrichment('proj-2', 'api-2', { cap: 'zip_code' });

    expect(httpMock.post).toHaveBeenCalledWith('/enrichment/estimate', {
      project_id: 'proj-2',
      api_id: 'api-2',
      param_mapping: { cap: 'zip_code' },
      file_source_id: null,
    });
    expect(result).toEqual({ estimated_api_calls: 10 });
  });

  it('executeEnrichmentAsync keeps force_recalculate and format defaults aligned', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 'task-9' } });

    const { executeEnrichmentAsync } = await import('../../src/api/enrichment');
    const result = await executeEnrichmentAsync('proj-3', { force_recalculate: true });

    expect(httpMock.post).toHaveBeenCalledWith('/projects/proj-3/enrich-async', {
      force_recalculate: true,
      format: 'csv',
    });
    expect(result).toEqual({ task_id: 'task-9' });
  });
});
