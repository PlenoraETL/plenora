import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { navigateMock, httpMock, resetSessionMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  httpMock: {
    post: vi.fn(),
    get: vi.fn(() => Promise.reject(new Error('non usato'))),
  },
  resetSessionMock: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));

vi.mock('../../src/utils/authBus', () => ({
  authBus: {
    onUnauthorized: null,
    emitBeforeLogout: vi.fn(),
  },
}));

// Il logout svuota store e cache con UNA chiamata (utils/session).
vi.mock('../../src/utils/session', () => ({ resetSession: resetSessionMock }));

import { MemoryRouter } from 'react-router-dom';
import { AuthProvider, useAuth } from '../../src/context/AuthContext';
import { authBus } from '../../src/utils/authBus';

function AuthProbe() {
  const { user, token, login, logout } = useAuth();

  return (
    <div>
      <div data-testid="token">{token || ''}</div>
      <div data-testid="username">{user?.username || ''}</div>
      <div data-testid="role">{user?.role || ''}</div>
      <button type="button" onClick={() => login('alice', 'secret')}>
        login
      </button>
      <button type="button" onClick={() => logout()}>
        logout
      </button>
      <button type="button" onClick={() => authBus.onUnauthorized?.()}>
        unauthorized
      </button>
    </div>
  );
}

describe('AuthContext contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it('persists login data, exposes them to consumers and clears everything on unauthorized logout', async () => {
    httpMock.post
      .mockResolvedValueOnce({
        data: {
          access_token: 'jwt-123',
          user_id: 'user-1',
          username: 'alice',
          group_id: 'group-1',
          role: 'admin',
        },
      })
      // Eventuale chiamata a /auth/logout (triggered da authBus
      // onUnauthorized → logout()). Non viene verificata qui — vive
      // nel test dedicato piu' sotto.
      .mockResolvedValue({ data: { ok: true, blacklisted: true, ttl: 28800 } });
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthProbe />
        </AuthProvider>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole('button', { name: 'login' }));

    await waitFor(() => {
      expect(httpMock.post).toHaveBeenCalledWith('/auth/login', {
        username: 'alice',
        password: 'secret',
      });
      expect(screen.getByTestId('token')).toHaveTextContent('jwt-123');
      expect(screen.getByTestId('username')).toHaveTextContent('alice');
      expect(screen.getByTestId('role')).toHaveTextContent('admin');
    });

    expect(sessionStorage.getItem('urby_token')).toBe('jwt-123');
    expect(JSON.parse(sessionStorage.getItem('urby_user'))).toEqual({
      user_id: 'user-1',
      username: 'alice',
      group_id: 'group-1',
      role: 'admin',
    });

    await user.click(screen.getByRole('button', { name: 'unauthorized' }));

    await waitFor(() => {
      expect(screen.getByTestId('token')).toHaveTextContent('');
      expect(screen.getByTestId('username')).toHaveTextContent('');
      expect(resetSessionMock).toHaveBeenCalledTimes(1);
      // R7.5: il rimando a /login ricorda la pagina da cui si e' usciti.
      expect(navigateMock).toHaveBeenCalledWith('/login', expect.objectContaining({ replace: true, state: { from: expect.objectContaining({ pathname: expect.any(String) }) } }));
    });

    expect(sessionStorage.getItem('urby_token')).toBeNull();
    expect(sessionStorage.getItem('urby_user')).toBeNull();
  });

  // Logout → POST /auth/logout (best-effort) + clear locale

  it('calls POST /auth/logout with bearer header on logout and clears session', async () => {
    httpMock.post
      .mockResolvedValueOnce({
        data: {
          access_token: 'jwt-999',
          user_id: 'u1',
          username: 'bob',
          group_id: 'g1',
          role: 'user',
        },
      })
      .mockResolvedValueOnce({
        data: { ok: true, blacklisted: true, ttl: 28800 },
      });

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthProbe />
        </AuthProvider>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole('button', { name: 'login' }));
    await waitFor(() => expect(screen.getByTestId('token')).toHaveTextContent('jwt-999'));
    expect(sessionStorage.getItem('urby_token')).toBe('jwt-999');

    await user.click(screen.getByRole('button', { name: 'logout' }));

    // La chiamata al backend deve essere stata fatta con il bearer
    // del token corrente (prima che sessionStorage venga svuotato).
    await waitFor(() => {
      expect(httpMock.post).toHaveBeenCalledTimes(2);
    });
    expect(httpMock.post).toHaveBeenNthCalledWith(
      2,
      '/auth/logout',
      null,
      { headers: { Authorization: 'Bearer jwt-999' } },
    );

    // Clear locale completato
    expect(sessionStorage.getItem('urby_token')).toBeNull();
    expect(sessionStorage.getItem('urby_user')).toBeNull();
    expect(screen.getByTestId('token')).toHaveTextContent('');
    expect(resetSessionMock).toHaveBeenCalledTimes(1);
  });

  it('still clears session locally when backend /auth/logout rejects', async () => {
    httpMock.post
      .mockResolvedValueOnce({
        data: {
          access_token: 'jwt-500',
          user_id: 'u1',
          username: 'carol',
          group_id: 'g1',
          role: 'user',
        },
      })
      // Server giu' / Redis down → 503 → Promise.reject
      .mockRejectedValueOnce(new Error('Service Unavailable'));

    // Il test helper console.error guard: il logout fallito produce
    // un `[API Error]` console.error dall'interceptor — lo consentiamo
    // per questo test specifico (verifichiamo proprio resilienza).
    globalThis.__frontendConsolePolicy.allowError = true;

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthProbe />
        </AuthProvider>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole('button', { name: 'login' }));
    await waitFor(() => expect(screen.getByTestId('token')).toHaveTextContent('jwt-500'));

    await user.click(screen.getByRole('button', { name: 'logout' }));

    // Anche se il server ha risposto 503, il clear locale DEVE
    // avvenire lo stesso (fire-and-forget). L'utente non deve
    // restare loggato se il backend e' down.
    await waitFor(() => {
      expect(sessionStorage.getItem('urby_token')).toBeNull();
      expect(screen.getByTestId('token')).toHaveTextContent('');
      expect(resetSessionMock).toHaveBeenCalledTimes(1);
    });
  });

  it('does not call /auth/logout when no token is stored', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthProbe />
        </AuthProvider>
      </MemoryRouter>,
    );

    // Nessun login prima del logout
    await user.click(screen.getByRole('button', { name: 'logout' }));

    // Nessuna richiesta al backend: non c'e' nulla da revocare.
    expect(httpMock.post).not.toHaveBeenCalled();
    // Il clear locale e' no-op ma resetSession parte lo stesso (cache in-memory).
    expect(resetSessionMock).toHaveBeenCalledTimes(1);
  });
});

// T3 / R5.3 + R5.9
describe('AuthContext: profilo dal server e value stabile', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it('R5.3: alla ripresa della sessione ruolo e gruppo vengono da GET /auth/me', async () => {
    sessionStorage.setItem('urby_token', 'jwt-1');
    sessionStorage.setItem('urby_user', JSON.stringify({ user_id: 'u1', username: 'alice', group_id: 'g1', role: 'admin' }));
    httpMock.get.mockResolvedValueOnce({ data: { id: 'u1', username: 'alice', group_id: 'g2', role: 'user' } });

    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthProbe />
        </AuthProvider>
      </MemoryRouter>,
    );

    expect(screen.getByTestId('role')).toHaveTextContent('admin');
    await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent('user'));
    expect(httpMock.get).toHaveBeenCalledWith('/auth/me');
    expect(JSON.parse(sessionStorage.getItem('urby_user'))).toEqual({
      user_id: 'u1', username: 'alice', group_id: 'g2', role: 'user',
    });
  });

  it('R5.3: senza sessione non chiama /auth/me', async () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <AuthProbe />
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(httpMock.get).not.toHaveBeenCalled();
  });

  it('R5.9: un re-render del provider non cambia il value (i consumer non si ri-renderizzano)', () => {
    sessionStorage.setItem('urby_token', 'jwt-1');
    sessionStorage.setItem('urby_user', JSON.stringify({ user_id: 'u1', username: 'alice', role: 'user' }));
    const seen = [];
    function Capture() {
      seen.push(useAuth());
      return null;
    }
    const MemoCapture = React.memo(Capture);
    const parseSpy = vi.spyOn(JSON, 'parse');
    const tree = (n) => (
      <MemoryRouter>
        <AuthProvider>
          <span>{n}</span>
          <MemoCapture />
        </AuthProvider>
      </MemoryRouter>
    );
    const { rerender } = render(tree(1));
    const parsesAfterMount = parseSpy.mock.calls.length;
    rerender(tree(2));
    rerender(tree(3));
    const parsesAfterRerender = parseSpy.mock.calls.length;
    parseSpy.mockRestore();

    expect(seen).toHaveLength(1);
    // Lo storage si legge solo al primo render.
    expect(parsesAfterMount).toBeGreaterThan(0);
    expect(parsesAfterRerender).toBe(parsesAfterMount);
  });
});
