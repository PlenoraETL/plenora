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

  it('preserves existing object-detail fallback behavior for specialized consumers', async () => {
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

    expect(error.message).toBe(detail);
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
      config: { url: '/api/projects', method: 'get' },
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
