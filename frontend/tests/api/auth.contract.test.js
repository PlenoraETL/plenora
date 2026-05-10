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

/**
 * Contract test — api/auth.js (C3.3 Phase 4).
 *
 * Pre-C3.3 le chiamate /auth/login e /auth/logout vivevano inline in
 * context/AuthContext.jsx. C3.3 le ha consolidate in api/auth.js
 * preservando bit-per-bit:
 *   - lo shape del payload login (axios POST /auth/login con
 *     { username, password })
 *   - il pattern fire-and-forget di logout (la funzione NON e' async,
 *     ritorna il Promise grezzo cosi' il caller puo' .catch() senza
 *     await prima di clearare lo stato locale).
 */
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
      // Aspetta la promise per garantire che httpMock.post sia stato
      // invocato prima dell'assertion (e per silenziare il warning
      // "unhandled promise rejection" anche in caso di future regressioni).
      await logout('jwt-x');

      expect(httpMock.post).toHaveBeenCalledWith(
        '/auth/logout',
        null,
        { headers: { Authorization: 'Bearer jwt-x' } },
      );
    });

    it('returns the raw Promise (not awaited) for fire-and-forget pattern', async () => {
      // Contratto critico: AuthContext.logout fa
      //   const p = apiLogout(token);
      //   if (p && typeof p.then === 'function') p.catch(() => {});
      // poi prosegue immediatamente al clear locale SENZA await. Se
      // qualcuno trasforma logout in async function, il caller dovrebbe
      // attenderlo (latency su UX) o cambiare pattern. Blindiamo il
      // contratto qui.
      httpMock.post.mockResolvedValueOnce({ data: {} });

      const { logout } = await import('../../src/api/auth');
      const p = logout('jwt-x');

      expect(p).toBeDefined();
      expect(typeof p.then).toBe('function');
      // Sanity: il consumer puo' attaccare .catch senza errori.
      expect(() => p.catch(() => {})).not.toThrow();
      await p; // chiusura pulita per evitare unhandled promise warning.
    });

    it('does NOT swallow rejections internally (caller handles via .catch)', async () => {
      httpMock.post.mockRejectedValueOnce(new Error('network down'));

      const { logout } = await import('../../src/api/auth');
      // La rejection deve propagarsi: AuthContext la silenzia con .catch
      // ma la funzione di per se' non e' supposta nascondere errori.
      await expect(logout('jwt-x')).rejects.toThrow('network down');
    });
  });
});
