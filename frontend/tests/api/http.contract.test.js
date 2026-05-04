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
});
