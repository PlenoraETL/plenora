/**
 * Sessione 2 P0 — createProject dual-call POST+PUT idempotency.
 *
 * Bug originale: la sequenza `POST /projects` + `PUT /projects/{id}/canvas`
 * era dual-call non idempotente. Se POST riusciva (progetto creato)
 * ma PUT canvas falliva per timeout, la funzione sollevava un'eccezione
 * → l'utente vedeva "errore creazione" e riprovava → DUPLICATO nel DB.
 *
 * Post-fix: la PUT canvas e' best-effort, errore propagato come flag
 * `canvasInitFailed` nella response invece di sollevare. Il caller
 * vede che il progetto esiste e puo' guidare l'utente all'editor.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

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


describe('createProject best-effort canvas init (sessione 2 P0)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('ritorna response.data anche se PUT canvas fallisce per timeout', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { metadata: { id: 'proj-1' } },
    });
    httpMock.put.mockRejectedValueOnce(new Error('Network timeout PUT canvas'));

    const { createProject } = await import('../../src/api/projects');

    // Pre-fix: questa chiamata sollevava → l'utente riprovava → duplicato
    const result = await createProject('My Project', 'desc', 'estrai', 'conn-1');

    // Post-fix: ritorna sempre il payload del POST (progetto esiste in DB)
    expect(result).toBeDefined();
    expect(result.metadata).toEqual({ id: 'proj-1' });
    // Con flag esplicito per il caller
    expect(result.canvasInitFailed).toBe(true);
    expect(result.canvasInitError).toContain('Network timeout');
  });

  it('ritorna response.data senza canvasInitFailed se PUT riesce', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { metadata: { id: 'proj-2' } },
    });
    httpMock.put.mockResolvedValueOnce({ data: {} });

    const { createProject } = await import('../../src/api/projects');
    const result = await createProject('Project OK', '', 'estrai', 'conn-2');

    expect(result.metadata).toEqual({ id: 'proj-2' });
    expect(result.canvasInitFailed).toBeUndefined();
  });

  it('skippa la PUT canvas per type != estrai (no opportunita di fallimento)', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { metadata: { id: 'proj-3' } },
    });

    const { createProject } = await import('../../src/api/projects');
    await createProject('Arricchisci', '', 'arricchisci', 'conn-3');

    expect(httpMock.put).not.toHaveBeenCalled();
  });

  it('skippa la PUT canvas se connectionId e null', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { metadata: { id: 'proj-4' } },
    });

    const { createProject } = await import('../../src/api/projects');
    await createProject('No conn', '', 'estrai', null);

    expect(httpMock.put).not.toHaveBeenCalled();
  });
});
