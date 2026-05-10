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

describe('api/auth contract', () => {
  beforeEach(() => {
    // resetAllMocks (non clearAllMocks) per pulire ANCHE le code di
    // mockResolvedValueOnce / mockRejectedValueOnce: clearAllMocks
    // azzera solo la call history, non gli implementation queue.
    vi.resetAllMocks();
  });

  describe('login', () => {
    it('posts /auth/login with { username, password } and returns body data', async () => {
      httpMock.post.mockResolvedValueOnce({
        data: {
          access_token: 'jwt-x',
          user_id: 'u-1',
          username: 'alice',
          group_id: 'g-1',
          role: 'admin',
        },
      });

      const { login } = await import('../../src/api/auth');
      const result = await login('alice', 'pwd');

      expect(httpMock.post).toHaveBeenCalledWith('/auth/login', {
        username: 'alice',
        password: 'pwd',
      });
      expect(result.access_token).toBe('jwt-x');
      expect(result.user_id).toBe('u-1');
      expect(result.role).toBe('admin');
    });

    it('propagates AxiosError unchanged on backend failure (e.g. 401)', async () => {
      const err = { response: { status: 401, data: { detail: 'bad creds' } } };
      httpMock.post.mockRejectedValueOnce(err);

      const { login } = await import('../../src/api/auth');
      await expect(login('alice', 'wrong')).rejects.toBe(err);
    });

    it('adds mfa_code only when supplied', async () => {
      httpMock.post.mockResolvedValueOnce({ data: { access_token: 'jwt-mfa' } });

      const { login } = await import('../../src/api/auth');
      await login('alice', 'pwd', '123456');

      expect(httpMock.post).toHaveBeenCalledWith('/auth/login', {
        username: 'alice',
        password: 'pwd',
        mfa_code: '123456',
      });
    });
  });

  describe('logout', () => {
    it('posts /auth/logout with explicit Authorization header', async () => {
      httpMock.post.mockResolvedValueOnce({ data: { ok: true } });

      const { logout } = await import('../../src/api/auth');
      await logout('jwt-x');

      expect(httpMock.post).toHaveBeenCalledWith(
        '/auth/logout',
        null,
        { headers: { Authorization: 'Bearer jwt-x' } },
      );
    });

    it('returns the raw Promise (not awaited) for fire-and-forget pattern', async () => {
      httpMock.post.mockResolvedValueOnce({ data: {} });

      const { logout } = await import('../../src/api/auth');
      const p = logout('jwt-x');

      expect(p).toBeDefined();
      expect(typeof p.then).toBe('function');
      expect(() => p.catch(() => {})).not.toThrow();
      await p;
    });

    it('does NOT swallow rejections internally (caller handles via .catch)', async () => {
      httpMock.post.mockRejectedValueOnce(new Error('network down'));

      const { logout } = await import('../../src/api/auth');
      await expect(logout('jwt-x')).rejects.toThrow('network down');
    });
  });
});
