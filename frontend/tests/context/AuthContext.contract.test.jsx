import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { navigateMock, httpMock, emitLogoutMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  httpMock: {
    post: vi.fn(),
  },
  emitLogoutMock: vi.fn(),
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
    emitLogout: emitLogoutMock,
    addLogoutListener: vi.fn(() => () => {}),
  },
}));

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
      expect(emitLogoutMock).toHaveBeenCalledTimes(1);
      expect(navigateMock).toHaveBeenCalledWith('/login', { replace: true });
    });

    expect(sessionStorage.getItem('urby_token')).toBeNull();
    expect(sessionStorage.getItem('urby_user')).toBeNull();
  });

  // =========================================================================
  // Logout → POST /auth/logout (best-effort) + clear locale
  // =========================================================================

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
    expect(emitLogoutMock).toHaveBeenCalledTimes(1);
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
      expect(emitLogoutMock).toHaveBeenCalledTimes(1);
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
    // Il clear locale e' no-op ma emitLogout parte lo stesso (cache in-memory).
    expect(emitLogoutMock).toHaveBeenCalledTimes(1);
  });
});
