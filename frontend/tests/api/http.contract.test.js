import { beforeEach, describe, expect, it, vi } from 'vitest';

const { axiosMock, requestUse, responseUse } = vi.hoisted(() => {
  const requestUse = vi.fn();
  const responseUse = vi.fn();
  return {
    requestUse,
    responseUse,
    axiosMock: {
      create: vi.fn(() => ({
        interceptors: {
          request: { use: requestUse },
          response: { use: responseUse },
        },
      })),
    },
  };
});

vi.mock('axios', () => ({
  default: axiosMock,
}));

describe('api/http error interceptor contract', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    sessionStorage.clear();
    globalThis.__frontendConsolePolicy.allowError = true;
  });

  async function loadRejectedInterceptor() {
    await import('../../src/api/http');
    expect(responseUse).toHaveBeenCalledTimes(1);
    return responseUse.mock.calls[0][1];
  }

  it('prefers new envelope message over legacy detail and attaches error.code', async () => {
    const onRejected = await loadRejectedInterceptor();
    const error = {
      message: 'Axios fallback',
      config: { url: '/api/example', method: 'get' },
      response: {
        status: 400,
        data: {
          error: 'VALIDATION_ERROR',
          message: 'Envelope message',
          detail: 'Legacy mirror',
        },
      },
    };

    await expect(onRejected(error)).rejects.toBe(error);

    expect(error.message).toBe('Envelope message');
    expect(error.code).toBe('VALIDATION_ERROR');
  });

  it('keeps legacy FastAPI detail fallback when message is absent', async () => {
    const onRejected = await loadRejectedInterceptor();
    const error = {
      message: 'Axios fallback',
      config: { url: '/api/example', method: 'post' },
      response: {
        status: 422,
        data: { detail: 'Legacy detail' },
      },
    };

    await expect(onRejected(error)).rejects.toBe(error);

    expect(error.message).toBe('Legacy detail');
    expect(error.code).toBeUndefined();
  });

  it('decodes JSON envelopes returned as Blob by download requests', async () => {
    const onRejected = await loadRejectedInterceptor();
    const error = {
      message: 'Request failed with status code 410',
      config: { url: '/api/tasks/task-1/download', method: 'get' },
      response: {
        status: 410,
        data: new Blob([
          JSON.stringify({
            error: 'http_410',
            message: "Il risultato non e' piu' disponibile",
          }),
        ], { type: 'application/json' }),
      },
    };

    await expect(onRejected(error)).rejects.toBe(error);

    expect(error.message).toBe("Il risultato non e' piu' disponibile");
    expect(error.code).toBe('http_410');
    expect(error.response.data.error).toBe('http_410');
  });

  it('normalizes legacy object detail to a scalar message', async () => {
    const onRejected = await loadRejectedInterceptor();
    const detail = {
      error: 'project_cleanup_partial',
      message: 'Some dependencies remain',
      dependencies: [{ type: 'schedule', id: 1 }],
    };
    const error = {
      message: 'Axios fallback',
      config: { url: '/api/projects/1', method: 'delete' },
      response: {
        status: 409,
        data: { detail },
      },
    };

    await expect(onRejected(error)).rejects.toBe(error);

    expect(error.message).toBe(detail.message);
    expect(error.response.data.detail).toBe(detail);
  });

  it.each([
    ['canvas_corrupted', 500, 'Canvas del progetto corrotto. Contattare amministratore.'],
    ['service_not_initialized', 503, 'Servizio non inizializzato. Riprovare o contattare admin.'],
    ['object_storage_disabled', 503, 'Object storage non configurato o disabilitato.'],
    ['aws_credentials_expired', 503, 'Credenziali AWS scadute o non valide.'],
    ['external_service_unreachable', 502, 'Servizio esterno non raggiungibile.'],
  ])('preserves critical backend envelope %s as actionable error', async (code, status, message) => {
    const onRejected = await loadRejectedInterceptor();
    const error = {
      message: 'Axios fallback',
      config: { url: '/api/critical-contract', method: 'get' },
      response: {
        status,
        data: {
          error: code,
          message,
          detail: { error: code, message },
        },
      },
    };

    await expect(onRejected(error)).rejects.toBe(error);

    expect(error.message).toBe(message);
    expect(error.code).toBe(code);
    expect(error.response.status).toBe(status);
    expect(error.response.data.detail.message).toBe(message);
  });

  it.each([
    ['auth_token_missing', 'Token mancante'],
    ['auth_token_invalid', 'Token invalido o scaduto'],
  ])('clears session and emits unauthorized for critical auth envelope %s', async (code, message) => {
    const onRejected = await loadRejectedInterceptor();
    const { authBus } = await import('../../src/utils/authBus');
    authBus.onUnauthorized = vi.fn();
    sessionStorage.setItem('urby_token', 'stale-token');
    sessionStorage.setItem('urby_user', '{"id":"u1"}');
    const error = {
      message: 'Axios fallback',
      config: { url: '/api/projects', method: 'get', headers: { Authorization: 'Bearer stale-token' } },
      response: {
        status: 401,
        data: {
          error: code,
          message,
          detail: { error: code, message },
        },
      },
    };

    await expect(onRejected(error)).rejects.toBe(error);

    expect(error.message).toBe(message);
    expect(error.code).toBe(code);
    expect(sessionStorage.getItem('urby_token')).toBeNull();
    expect(sessionStorage.getItem('urby_user')).toBeNull();
    expect(authBus.onUnauthorized).toHaveBeenCalledTimes(1);
  });
});

// T3 / R5.2 + N9: il 401 chiude la sessione una volta sola, e solo la
// sessione che ha fatto la richiesta.
describe('api/http 401: una sola chiusura per la sessione che ha fatto la richiesta', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    sessionStorage.clear();
    globalThis.__frontendConsolePolicy.allowError = true;
  });

  async function setup() {
    await import('../../src/api/http');
    const onRejected = responseUse.mock.calls[0][1];
    const { authBus } = await import('../../src/utils/authBus');
    authBus.onUnauthorized = vi.fn();
    return { onRejected, authBus };
  }

  const expired = (token, url = '/api/projects') => ({
    message: 'Request failed with status code 401',
    config: { url, method: 'get', headers: { Authorization: `Bearer ${token}` } },
    response: { status: 401, data: { error: 'auth_token_invalid', message: 'Token invalido o scaduto' } },
  });

  it('N richieste parallele scadute: logout una volta sola', async () => {
    const { onRejected, authBus } = await setup();
    sessionStorage.setItem('urby_token', 'A');
    sessionStorage.setItem('urby_user', '{"id":"u1"}');

    await Promise.allSettled([1, 2, 3, 4].map(() => onRejected(expired('A'))));

    expect(authBus.onUnauthorized).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem('urby_token')).toBeNull();
  });

  it('un 401 tardivo della sessione precedente non tocca la sessione nuova', async () => {
    const { onRejected, authBus } = await setup();
    // L'utente e' uscito e rientrato: la sessione attuale e' B.
    sessionStorage.setItem('urby_token', 'B');
    sessionStorage.setItem('urby_user', '{"id":"u2"}');

    await expect(onRejected(expired('A'))).rejects.toBeTruthy();

    expect(authBus.onUnauthorized).not.toHaveBeenCalled();
    expect(sessionStorage.getItem('urby_token')).toBe('B');
    expect(sessionStorage.getItem('urby_user')).toBe('{"id":"u2"}');
  });

  it.each(['/auth/login', '/auth/logout'])('un 401 su %s non chiude la sessione', async (url) => {
    const { onRejected, authBus } = await setup();
    sessionStorage.setItem('urby_token', 'A');

    await expect(onRejected(expired('A', url))).rejects.toBeTruthy();

    expect(authBus.onUnauthorized).not.toHaveBeenCalled();
    expect(sessionStorage.getItem('urby_token')).toBe('A');
  });

  it('il messaggio non e\' mai il testo grezzo di axios', async () => {
    const { onRejected } = await setup();
    const cases = [
      [{ message: 'Network Error', code: 'ERR_NETWORK', isAxiosError: true, config: { url: '/x' } }, /Impossibile contattare il server/],
      [{ message: 'timeout of 30000ms exceeded', code: 'ECONNABORTED', isAxiosError: true, config: { url: '/x' } }, /non ha risposto in tempo/],
      [{ message: 'Request failed with status code 500', config: { url: '/x' }, response: { status: 500, data: '' } }, /errore interno/],
      [{ message: 'Request failed with status code 403', config: { url: '/x' }, response: { status: 403, data: {} } }, /permessi/],
      [{ message: 'Request failed with status code 404', config: { url: '/x' }, response: { status: 404, data: null } }, /non esiste/],
      [{ message: 'Request failed with status code 409', config: { url: '/x' }, response: { status: 409, data: {} } }, /conflitto/],
      [{ message: 'Request failed with status code 422', config: { url: '/x' }, response: { status: 422, data: { detail: [{ loc: ['body', 'name'], msg: 'Campo obbligatorio' }] } } }, /^body\.name: Campo obbligatorio$/],
    ];
    for (const [error, expected] of cases) {
      await expect(onRejected(error)).rejects.toBe(error);
      expect(error.message).toMatch(expected);
    }
  });
});
