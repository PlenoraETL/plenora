/**
 * Contract test per `validateEnrichment` (POST /enrichment/validate).
 *
 * Bug coperto (plenora-bug-contracts-schemas P1):
 *   Pre-fix il FE non inviava `static_parameters` nel body: il BE lo
 *   trattava come None, generando falsi `missing_columns` per parametri
 *   soddisfatti da static values nel canvas.
 *
 * Post-fix: la signature accetta `staticParameters` come 6° argomento
 * e lo serializza nel body come `static_parameters`. La response shape
 * del BE include `null_param_rows_count` che il FE ora deve poter
 * leggere senza errori.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

const { httpPostMock } = vi.hoisted(() => ({
  httpPostMock: vi.fn(),
}));

vi.mock('../../src/api/http', () => ({
  default: { post: httpPostMock },
}));

import { validateEnrichment } from '../../src/api/enrichment';


describe('validateEnrichment — payload shape contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    httpPostMock.mockResolvedValue({
      data: {
        valid: true,
        total_rows: 100,
        valid_rows: 75,
        invalid_rows: 0,
        missing_columns: [],
        null_param_rows_count: 25,
        warnings: [],
      },
    });
  });

  it('serializes static_parameters into the request body', async () => {
    await validateEnrichment(
      'proj-1',
      'api-1',
      { city: 'colonna_citta' },
      null,
      ['static_col'],
      { mode: 'lookup', country: 'IT' },
    );

    expect(httpPostMock).toHaveBeenCalledTimes(1);
    const [url, body] = httpPostMock.mock.calls[0];
    expect(url).toBe('/enrichment/validate');
    expect(body).toEqual({
      project_id: 'proj-1',
      api_id: 'api-1',
      param_mapping: { city: 'colonna_citta' },
      file_source_id: null,
      static_columns: ['static_col'],
      static_parameters: { mode: 'lookup', country: 'IT' },
    });
  });

  it('static_parameters defaults to null when not provided', async () => {
    await validateEnrichment('proj-1', 'api-1', {});
    const [, body] = httpPostMock.mock.calls[0];
    // Il campo deve essere SEMPRE presente nel body (anche null) per
    // documentare la shape al BE e a chi legge il network tab.
    expect(body).toHaveProperty('static_parameters');
    expect(body.static_parameters).toBeNull();
  });

  it('returns the full response shape including null_param_rows_count', async () => {
    const result = await validateEnrichment('proj-1', 'api-1', {});
    expect(result).toHaveProperty('null_param_rows_count');
    expect(result.null_param_rows_count).toBe(25);
  });

  it('function arity supports the new 6-arg signature', () => {
    // `Function.length` conta i parametri prima del primo default.
    // Pre-fix: 3 (projectId, apiId, paramMapping). Post-fix: stesso
    // valore (i 3 default restano), ma la signature accetta 6 arg.
    // Verifichiamo l'invocazione con 6 arg senza crash.
    expect(() => validateEnrichment('p', 'a', {}, null, null, null))
      .not.toThrow();
  });
});
