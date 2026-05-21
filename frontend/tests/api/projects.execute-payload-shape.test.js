/**
 * Contract test per `executeProjectQuery` (POST /projects/{id}/execute).
 *
 * Bug coperto (plenora-bug-contracts-schemas P0):
 *   Pre-fix la funzione aveva un branch legacy che accettava un
 *   `connection_id` come secondo argomento stringa e lo metteva nel
 *   payload. Il BE `ExecuteRequest` non ha questo campo: l'esecuzione
 *   ignorava silenziosamente il valore (drift contract).
 *
 * Post-fix: branch rimosso, signature accetta solo un oggetto options
 * (`force_recalculate`, `intermediate_mode`).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

const { httpPostMock } = vi.hoisted(() => ({
  httpPostMock: vi.fn(),
}));

vi.mock('../../src/api/http', () => ({
  default: { post: httpPostMock },
}));

import { executeProjectQuery } from '../../src/api/projects';


describe('executeProjectQuery — payload shape contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    httpPostMock.mockResolvedValue({ data: { task_id: 't-1', async: true } });
  });

  it('passes empty payload when no options provided', async () => {
    await executeProjectQuery('proj-1');
    expect(httpPostMock).toHaveBeenCalledTimes(1);
    const [url, payload] = httpPostMock.mock.calls[0];
    expect(url).toBe('/projects/proj-1/execute');
    expect(payload).toEqual({});
  });

  it('serializes force_recalculate from options object', async () => {
    await executeProjectQuery('proj-1', { force_recalculate: true });
    const [, payload] = httpPostMock.mock.calls[0];
    expect(payload).toEqual({ force_recalculate: true });
  });

  it('ignores legacy string second arg (no connection_id in payload)', async () => {
    // Pre-fix: passare una stringa produceva `payload = { connection_id: 'conn-x' }`,
    // accettato dall'API ma ignorato dal BE ExecuteRequest model.
    // Post-fix: la stringa viene ignorata, payload resta vuoto.
    await executeProjectQuery('proj-1', 'conn-x');
    const [, payload] = httpPostMock.mock.calls[0];
    expect(payload).not.toHaveProperty('connection_id');
    expect(payload).toEqual({});
  });

  it('adds intermediate_mode as query param, not body', async () => {
    await executeProjectQuery('proj-1', { intermediate_mode: 'full_pg' });
    const [, payload, config] = httpPostMock.mock.calls[0];
    expect(payload).toEqual({ force_recalculate: false });
    expect(config).toEqual({ params: { intermediate_mode: 'full_pg' } });
  });

  it('omits intermediate_mode config when not provided', async () => {
    await executeProjectQuery('proj-1', { force_recalculate: false });
    const callArgs = httpPostMock.mock.calls[0];
    // 2-arg call (url, payload) — no third config arg quando
    // intermediate_mode non e' valorizzato.
    expect(callArgs).toHaveLength(2);
  });
});
