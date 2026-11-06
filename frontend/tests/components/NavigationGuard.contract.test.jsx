/**
 * Contract: guard di navigazione UNICO (Fase 5, T4; R7.1 e nota
 * CommandPalette). Con un editor montato e modifiche non salvate
 * (hasUnsavedChanges), OGNI cambio di pagina chiede con il dialog del
 * progetto: link, navigate(), tasto Indietro del browser, Ctrl+K.
 * Il dialog ricorda la copia di sicurezza locale. Senza modifiche, o senza
 * editor montato, non chiede. Il logout vero (hooks/useSignOut) chiede UNA
 * volta e «Esci» arriva a /login senza seconda domanda.
 */
import React from 'react';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';

vi.mock('../../src/api/auth', () => ({ login: vi.fn(), logout: vi.fn(() => Promise.resolve()) }));
vi.mock('../../src/api/users', () => ({ getMe: vi.fn(() => new Promise(() => {})) }));
import { createMemoryRouter, RouterProvider, Link, Outlet, useNavigate } from 'react-router-dom';

import NavigationGuard from '../../src/components/common/NavigationGuard';
import CommandPalette from '../../src/components/common/CommandPalette';
import { ConfirmProvider } from '../../src/context/ConfirmContext';
import { useUnsavedChangesGuard } from '../../src/hooks/useUnsavedChangesGuard';
import { LEAVE_DIALOG, isLeaveGuardActive } from '../../src/utils/leaveGuard';
import { useSignOut } from '../../src/hooks/useSignOut';
import { AuthProvider } from '../../src/context/AuthContext';
import useCanvasStore from '../../src/store/useCanvasStore';
import { answerConfirm } from '../helpers/confirmDialog';

function Editor() {
  useUnsavedChangesGuard();
  // L'uscita vera (hooks/useSignOut), come i pulsanti «Esci» dell'app.
  const signOut = useSignOut();
  const navigate = useNavigate();
  return (
    <div>
      <h1>editor</h1>
      <Link to="/files">file</Link>
      <button type="button" onClick={() => navigate('/')}>indietro-app</button>
      <button type="button" onClick={signOut}>esci</button>
    </div>
  );
}

function Layout() {
  return (
    <AuthProvider>
      <ConfirmProvider>
        <NavigationGuard />
        <CommandPalette />
        <Outlet />
      </ConfirmProvider>
    </AuthProvider>
  );
}

function mount(initial = ['/', '/project/p1']) {
  const router = createMemoryRouter([{
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <h1>home</h1> },
      { path: 'project/:projectId', element: <Editor /> },
      { path: 'files', element: <h1>files</h1> },
      { path: 'login', element: <h1>login</h1> },
    ],
  }], { initialEntries: initial, initialIndex: initial.length - 1 });
  render(<RouterProvider router={router} />);
  return router;
}

const makeDirty = () => act(() => { useCanvasStore.getState().updateNodeData('n1', { label: 'cambiato' }); });

beforeEach(() => {
  const s = useCanvasStore.getState();
  s.reset();
  s.init('p1', 'manipola');
  s.loadGraph([{ id: 'n1', type: 'transform', position: { x: 0, y: 0 }, data: { label: 'a' } }], []);
  sessionStorage.setItem('urby_token', 't');
  sessionStorage.setItem('urby_user', JSON.stringify({ user_id: 1, username: 'u', role: 'admin' }));
});
afterEach(() => { cleanup(); sessionStorage.clear(); });

describe('NavigationGuard', () => {
  it('senza modifiche non chiede', async () => {
    mount();
    expect(isLeaveGuardActive()).toBe(true);
    await act(async () => { fireEvent.click(screen.getByText('file')); });
    expect(screen.getByRole('heading', { name: 'files' })).toBeInTheDocument();
  });

  it('link interno con modifiche: chiede, Resta annulla, Esci prosegue', async () => {
    mount();
    makeDirty();
    await act(async () => { fireEvent.click(screen.getByText('file')); });
    expect(screen.getByRole('dialog').textContent).toMatch(/copia di sicurezza/);
    expect(screen.getByRole('dialog').textContent).toContain(LEAVE_DIALOG.title);
    await answerConfirm(false);
    expect(screen.getByRole('heading', { name: 'editor' })).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByText('file')); });
    await answerConfirm(true, { confirmText: LEAVE_DIALOG.confirmText });
    expect(screen.getByRole('heading', { name: 'files' })).toBeInTheDocument();
  });

  it('tasto Indietro del browser con modifiche: chiede e Resta torna dov\'era', async () => {
    const router = mount();
    makeDirty();
    await act(async () => { router.navigate(-1); });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await answerConfirm(false);
    expect(router.state.location.pathname).toBe('/project/p1');
    expect(screen.getByRole('heading', { name: 'editor' })).toBeInTheDocument();
  });

  it('navigate() dell\'editor passa dal guard (niente doppia domanda)', async () => {
    mount();
    makeDirty();
    await act(async () => { fireEvent.click(screen.getByText('indietro-app')); });
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    await answerConfirm(true, { confirmText: LEAVE_DIALOG.confirmText });
    expect(screen.getByRole('heading', { name: 'home' })).toBeInTheDocument();
  });

  it('Ctrl+K: la palette naviga solo dopo la conferma', async () => {
    mount();
    makeDirty();
    await act(async () => { fireEvent.keyDown(window, { key: 'k', ctrlKey: true }); });
    const input = screen.getByRole('textbox', { name: /cerca un comando/i });
    fireEvent.change(input, { target: { value: 'File' } });
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }); });
    expect(screen.getByRole('dialog').textContent).toContain(LEAVE_DIALOG.title);
    await answerConfirm(false);
    expect(screen.getByRole('heading', { name: 'editor' })).toBeInTheDocument();
  });

  it('Ctrl+K non apre la palette sopra un\'altra finestra', async () => {
    mount();
    makeDirty();
    await act(async () => { fireEvent.click(screen.getByText('file')); });
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    await act(async () => { fireEvent.keyDown(window, { key: 'k', ctrlKey: true }); });
    expect(screen.queryByRole('textbox', { name: /cerca un comando/i })).toBeNull();
    await answerConfirm(false);
  });

  it('logout con modifiche: UNA sola domanda; Resta resta nell\'editor con la sessione',async () => {
    const router = mount();
    makeDirty();
    await act(async () => { fireEvent.click(screen.getByText('esci')); });
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(screen.getByRole('dialog').textContent).toContain(LEAVE_DIALOG.title);
    await answerConfirm(false);
    expect(screen.queryAllByRole('dialog')).toHaveLength(0);
    expect(router.state.location.pathname).toBe('/project/p1');
    expect(sessionStorage.getItem('urby_token')).toBe('t');
  });

  it('logout con modifiche: «Esci» porta al login senza una seconda domanda (niente blocker su /login)', async () => {
    const router = mount();
    makeDirty();
    await act(async () => { fireEvent.click(screen.getByText('esci')); });
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    await answerConfirm(true, { confirmText: LEAVE_DIALOG.confirmText });
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    expect(screen.queryAllByRole('dialog')).toHaveLength(0);
    expect(router.state.location.pathname).toBe('/login');
    expect(router.state.location.state?.from).toBeUndefined();
    expect(screen.getByRole('heading', { name: 'login' })).toBeInTheDocument();
    expect(sessionStorage.getItem('urby_token')).toBeNull();
  });

  it('logout senza modifiche: nessuna domanda, subito al login', async () => {
    const router = mount();
    await act(async () => { fireEvent.click(screen.getByText('esci')); });
    expect(screen.queryAllByRole('dialog')).toHaveLength(0);
    expect(router.state.location.pathname).toBe('/login');
  });

  it('fuori dall\'editor il guard non agisce', async () => {
    const router = mount(['/files']);
    makeDirty();
    expect(isLeaveGuardActive()).toBe(false);
    await act(async () => { router.navigate('/'); });
    expect(screen.getByRole('heading', { name: 'home' })).toBeInTheDocument();
  });
});
