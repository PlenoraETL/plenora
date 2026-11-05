/**
 * Contract: dove si torna dopo il login (regola unica
 * utils/navigation.loginRedirectState; Fase 5, correzione dopo la conferma
 * di T2+T4, punto 2). Con l'app vera (createAppRouter: rotte, guardie,
 * AuthProvider, LoginPage):
 *  - uscita volontaria da /project/7 -> login -> home (mai la pagina
 *    dell'utente precedente);
 *  - 401 su /project/7 -> login -> /project/7;
 *  - pagina chiesta senza sessione -> login -> quella pagina.
 */
import React from 'react';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { apiLogin } = vi.hoisted(() => ({ apiLogin: vi.fn() }));

vi.mock('../../src/api/auth', () => ({ login: apiLogin, logout: vi.fn(() => Promise.resolve()) }));
vi.mock('../../src/api/users', () => ({ getMe: vi.fn(() => new Promise(() => {})) }));
vi.mock('../../src/pages/ProjectList', () => ({ default: () => <h1>home</h1> }));
vi.mock('../../src/pages/EstraiEditor', async () => {
  const { useSignOut } = await import('../../src/hooks/useSignOut');
  function Editor() {
    const signOut = useSignOut();
    return (<div><h1>progetto 7</h1><button type="button" onClick={signOut}>Esci</button></div>);
  }
  return { default: Editor };
});
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));

import { RouterProvider } from 'react-router-dom';
import { createAppRouter } from '../../src/App';
import { authBus } from '../../src/utils/authBus';

const USER = { user_id: 1, username: 'mario', group_id: 1, role: 'admin' };

function start(path, { session = true } = {}) {
  if (session) {
    sessionStorage.setItem('urby_token', 't-old');
    sessionStorage.setItem('urby_user', JSON.stringify(USER));
  }
  window.history.replaceState(null, '', path);
  const router = createAppRouter();
  render(<RouterProvider router={router} future={{ v7_startTransition: true }} />);
  return router;
}

async function loginAs() {
  apiLogin.mockResolvedValueOnce({ access_token: 't-new', ...USER, username: 'luigi' });
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'luigi' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password-lunga-1' } });
  await act(async () => { fireEvent.submit(screen.getByLabelText('Username').closest('form')); });
}

beforeEach(() => {
  sessionStorage.clear();
  // Il canvas animato della pagina di login non serve qui.
  const ctx = new Proxy({}, { get: (t, k) => (k in t ? t[k] : () => ({ addColorStop: () => {} })), set: (t, k, v) => { t[k] = v; return true; } });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx);
});
afterEach(() => { cleanup(); sessionStorage.clear(); authBus.clearExit(); });

describe('ritorno dopo il login', () => {
  it('logout volontario da /project/7 -> login -> home', async () => {
    const router = start('/project/7');
    expect(await screen.findByRole('heading', { name: 'progetto 7' })).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Esci' })); });
    expect(router.state.location.pathname).toBe('/login');
    expect(router.state.location.state?.from).toBeUndefined();
    await loginAs();
    expect(await screen.findByRole('heading', { name: 'home' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/');
  });

  it('401 su /project/7 -> login -> /project/7', async () => {
    const router = start('/project/7');
    expect(await screen.findByRole('heading', { name: 'progetto 7' })).toBeInTheDocument();
    await act(async () => { authBus.onUnauthorized(); });
    expect(router.state.location.pathname).toBe('/login');
    expect(router.state.location.state?.from?.pathname).toBe('/project/7');
    await loginAs();
    expect(await screen.findByRole('heading', { name: 'progetto 7' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/project/7');
  });

  it('pagina chiesta senza sessione -> login -> quella pagina', async () => {
    const router = start('/project/7', { session: false });
    await screen.findByLabelText('Username');
    expect(router.state.location.state?.from?.pathname).toBe('/project/7');
    await loginAs();
    expect(await screen.findByRole('heading', { name: 'progetto 7' })).toBeInTheDocument();
  });

  it('dopo un logout e un nuovo login, un 401 ricorda di nuovo la pagina', async () => {
    const router = start('/project/7');
    await screen.findByRole('heading', { name: 'progetto 7' });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Esci' })); });
    await loginAs();
    await screen.findByRole('heading', { name: 'home' });
    await act(async () => { await router.navigate('/project/7'); });
    await screen.findByRole('heading', { name: 'progetto 7' });
    await act(async () => { authBus.onUnauthorized(); });
    expect(router.state.location.state?.from?.pathname).toBe('/project/7');
  });
});
