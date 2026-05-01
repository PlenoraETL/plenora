/**
 * Contract test — R-25 PR-B5: param `intermediate_mode` opt-in superadmin.
 *
 * Garantisce che i due API client (executeProjectQuery, executeEnrichmentAsync)
 * serializzino il param SOLO se valorizzato. Senza il param i test esistenti
 * (enrichment.contract.test.js) continuano a passare con la signature a 2
 * argomenti su http.post — qui validiamo esplicitamente il branching e che
 * il body POST resti pulito (intermediate_mode NON deve finire nel body).
 *
 * Il backend (orchestrator/router.py:133-143 + arricchisci/router.py:792-801)
 * accetta solo i 4 valori del Literal. La UI restringe gia' la scelta via
 * <select> chiuso (ExecutionModal), ma qui copriamo il pass-through "opaco"
 * dell'API client per evitare regressioni future.
 */

import { vi, describe, it, expect, beforeEach } from 'vitest';

const { httpMock } = vi.hoisted(() => ({
  httpMock: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock('../../src/api/http', () => ({ default: httpMock }));

describe('executeProjectQuery — intermediate_mode pass-through', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('senza opts: POST a 2 argomenti, niente query param', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 't0' } });
    const { executeProjectQuery } = await import('../../src/api/projects');

    await executeProjectQuery('proj-A');

    expect(httpMock.post).toHaveBeenCalledTimes(1);
    expect(httpMock.post).toHaveBeenCalledWith('/projects/proj-A/execute', {});
  });

  it('con force_recalculate ma senza intermediate_mode: POST 2 args + body', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 't1' } });
    const { executeProjectQuery } = await import('../../src/api/projects');

    await executeProjectQuery('proj-B', { force_recalculate: true });

    expect(httpMock.post).toHaveBeenCalledWith('/projects/proj-B/execute', {
      force_recalculate: true,
    });
  });

  it('con intermediate_mode: POST 3 args con params query string', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 't2' } });
    const { executeProjectQuery } = await import('../../src/api/projects');

    await executeProjectQuery('proj-C', {
      force_recalculate: true,
      intermediate_mode: 'full_pg',
    });

    expect(httpMock.post).toHaveBeenCalledWith(
      '/projects/proj-C/execute',
      { force_recalculate: true },
      { params: { intermediate_mode: 'full_pg' } },
    );
  });

  it('intermediate_mode falsy (null/empty): NON serializza il param', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 't3a' } });
    const { executeProjectQuery } = await import('../../src/api/projects');

    await executeProjectQuery('proj-D', { intermediate_mode: null });
    expect(httpMock.post).toHaveBeenCalledWith('/projects/proj-D/execute', {
      force_recalculate: false,
    });

    httpMock.post.mockClear();
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 't3b' } });
    await executeProjectQuery('proj-D', { intermediate_mode: '' });
    expect(httpMock.post).toHaveBeenCalledWith('/projects/proj-D/execute', {
      force_recalculate: false,
    });
  });

  it('signature legacy connectionId stringa: invariata, niente intermediate_mode', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 't4' } });
    const { executeProjectQuery } = await import('../../src/api/projects');

    await executeProjectQuery('proj-E', 'conn-1');

    expect(httpMock.post).toHaveBeenCalledWith('/projects/proj-E/execute', {
      connection_id: 'conn-1',
    });
  });
});

describe('executeEnrichmentAsync — intermediate_mode pass-through', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('default: POST 2 args (compat con tests esistenti)', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 'e0' } });
    const { executeEnrichmentAsync } = await import('../../src/api/enrichment');

    await executeEnrichmentAsync('proj-X');

    expect(httpMock.post).toHaveBeenCalledWith('/projects/proj-X/enrich-async', {
      force_recalculate: false,
      format: 'csv',
    });
  });

  it('con intermediate_mode: POST 3 args con params query string', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 'e1' } });
    const { executeEnrichmentAsync } = await import('../../src/api/enrichment');

    await executeEnrichmentAsync('proj-Y', {
      force_recalculate: true,
      format: 'xlsx',
      intermediate_mode: 'metadata_only',
    });

    expect(httpMock.post).toHaveBeenCalledWith(
      '/projects/proj-Y/enrich-async',
      { force_recalculate: true, format: 'xlsx' },
      { params: { intermediate_mode: 'metadata_only' } },
    );
  });
});
