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

  it('preflightEnrichment: POST /projects/{id}/preflight with the run inputs, no canvas (the saved one)', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { valid: true, preparation_fingerprint: 'f1', nodes: [] } });

    const { preflightEnrichment } = await import('../../src/api/enrichment');
    const result = await preflightEnrichment('proj-1', { inputs: { anno: 2026 }, force_recalculate: true });

    expect(httpMock.post).toHaveBeenCalledWith('/projects/proj-1/preflight', {
      inputs: { anno: 2026 },
      force_recalculate: true,
    });
    expect(result.preparation_fingerprint).toBe('f1');
  });

  it('executeEnrichmentAsync sends the preflight fingerprint when given', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 'task-1' } });

    const { executeEnrichmentAsync } = await import('../../src/api/enrichment');
    await executeEnrichmentAsync('proj-1', { inputs: { anno: 2026 }, preparation_fingerprint: 'f1' });

    expect(httpMock.post).toHaveBeenCalledWith('/projects/proj-1/enrich-async', {
      force_recalculate: false,
      format: 'csv',
      inputs: { anno: 2026 },
      preparation_fingerprint: 'f1',
    });
  });

  it('the old validate/estimate calls are gone (one preparation: the preflight)', async () => {
    const api = await import('../../src/api/enrichment');
    expect(api.validateEnrichment).toBeUndefined();
    expect(api.estimateEnrichment).toBeUndefined();
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
