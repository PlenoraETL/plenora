import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import UserManager from '../../src/pages/UserManager';
import * as usersApi from '../../src/api/users';

const {
  navigateMock,
  toastMock,
  listUsersMock,
  createUserMock,
  listGroupsMock,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
  listUsersMock: vi.fn(),
  createUserMock: vi.fn(),
  listGroupsMock: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'admin-1', role: 'superadmin', group_id: 'grp-1', username: 'root' },
  }),
}));

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/api/users', () => ({
  listUsers: listUsersMock,
  createUser: createUserMock,
  updateUser: vi.fn(),
  deleteUser: vi.fn(),
  deleteUserKeepingHistory: vi.fn(),
  resetPassword: vi.fn(),
  listGroups: listGroupsMock,
  createGroup: vi.fn(),
  deleteGroup: vi.fn(),
  getGroupSummary: vi.fn(),
}));

vi.mock('../../src/api/http', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../src/components/ChangePasswordModal', () => ({
  default: () => null,
}));

vi.mock('../../src/components/AppSidebar', () => ({
  default: () => <div data-testid="app-sidebar" />,
}));

// Tabella reale dei permessi, con le sole capability del test forzate.
vi.mock('../../src/utils/permissions', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    can: {
      ...actual.can,
      manageUsers: () => true,
    },
    role: {
      ...actual.role,
      isSuperadmin: (user) => user?.role === 'superadmin',
      isAdminOrAbove: (user) => ['admin', 'superadmin'].includes(user?.role),
    },
  };
});

describe('UserManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listUsersMock.mockResolvedValue([
      { id: 'user-1', username: 'mario', role: 'user', group_id: 'grp-1', is_active: true },
    ]);
    listGroupsMock.mockResolvedValue([
      { group_id: 'grp-1', name: 'Group One' },
      { group_id: 'grp-2', name: 'Group Two' },
    ]);
    createUserMock.mockResolvedValue({ id: 'user-2' });
  });

  it('creates a new user with the modal payload expected by the backend', async () => {
    render(<UserManager />);

    expect(await screen.findByText('mario')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /\+ nuovo utente/i }));
    await userEvent.type(screen.getByPlaceholderText(/mario\.rossi/i), 'laura');
    await userEvent.type(screen.getByPlaceholderText(/almeno 12 caratteri/i), 'StrongPwd1234');
    await userEvent.selectOptions(screen.getAllByRole('combobox')[0], 'admin');
    await userEvent.selectOptions(screen.getAllByRole('combobox')[1], 'grp-2');
    await userEvent.click(screen.getByRole('button', { name: /crea utente/i }));

    await waitFor(() => {
      expect(createUserMock).toHaveBeenCalledWith({
        username: 'laura',
        password: 'StrongPwd1234',
        role: 'admin',
        group_id: 'grp-2',
        // T5 (R8.20): solo i campi che il backend accetta in creazione (UserCreate).
      });
    });
  });

  it('rejects a too short password before calling the backend (regola unica, 12)', async () => {
    render(<UserManager />);
    expect(await screen.findByText('mario')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /\+ nuovo utente/i }));
    await userEvent.type(screen.getByPlaceholderText(/mario\.rossi/i), 'laura');
    await userEvent.type(screen.getByPlaceholderText(/almeno 12 caratteri/i), 'Corta123');
    await userEvent.click(screen.getByRole('button', { name: /crea utente/i }));
    expect(await screen.findByText(/almeno 12 caratteri/)).toBeInTheDocument();
    expect(createUserMock).not.toHaveBeenCalled();
  });

  it('deletes a group only after the impact report is loaded and its NAME is typed (R10.3)', async () => {
    let resolveSummary;
    usersApi.getGroupSummary.mockImplementation(() => new Promise((resolve) => { resolveSummary = resolve; }));
    usersApi.deleteGroup.mockResolvedValue({ success: true, id: 'grp-2', report: { projects: 4, users: 2 } });
    render(<UserManager />);
    expect(await screen.findByText('mario')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /gruppi/i }));
    const deleteButtons = await screen.findAllByRole('button', { name: /elimina$/i });
    await userEvent.click(deleteButtons[deleteButtons.length - 1]);

    const confirm = screen.getByRole('button', { name: /elimina definitivamente/i });
    const nameInput = screen.getByLabelText('Nome del gruppo da eliminare');
    // Riepilogo in caricamento: niente conferma, campo spento.
    expect(nameInput).toBeDisabled();
    expect(confirm).toBeDisabled();

    resolveSummary({ projects: 4, users: 2, connections: 0, file_sources: 0, datasources: 0, api_connections: 0, schedules: 0 });
    await waitFor(() => expect(nameInput).not.toBeDisabled());
    expect(screen.getByText('Verrà eliminato')).toBeInTheDocument();

    // L'id non basta: serve il nome.
    await userEvent.type(nameInput, 'grp-2');
    expect(confirm).toBeDisabled();
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Group Two');
    expect(confirm).not.toBeDisabled();
    await userEvent.click(confirm);

    await waitFor(() => expect(usersApi.deleteGroup).toHaveBeenCalledWith('grp-2'));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith(expect.stringMatching(/4 progetti, 2 utenti/)));
  });

  it('cannot confirm a group deletion when the impact report fails', async () => {
    usersApi.getGroupSummary.mockRejectedValue(new Error('boom'));
    render(<UserManager />);
    expect(await screen.findByText('mario')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /gruppi/i }));
    const deleteButtons = await screen.findAllByRole('button', { name: /elimina$/i });
    await userEvent.click(deleteButtons[deleteButtons.length - 1]);
    expect(await screen.findByText(/senza sapere cosa verrà eliminato/)).toBeInTheDocument();
    expect(screen.getByLabelText('Nome del gruppo da eliminare')).toBeDisabled();
    expect(screen.getByRole('button', { name: /elimina definitivamente/i })).toBeDisabled();
  });

  // Lo storico non si perde (09/11/2026): l'esecutore E0071 ha lanciato un
  // Eseguibile; il backend rifiuta l'eliminazione (409 user_has_history) e la
  // pagina offre «Disattiva».
  it('utente con storico: mostra il messaggio del backend e offre «Disattiva»', async () => {
    listUsersMock.mockResolvedValue([
      { id: 'u-e0071', username: 'E0071', role: 'esecutore', group_id: 'grp-1', is_active: true },
    ]);
    const message = "L'utente «E0071» ha 1 esecuzione registrata: per non perdere lo storico non si può eliminare. Disattivalo.";
    usersApi.deleteUser.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { error: 'user_has_history', message, details: { references: [{ count: 1 }] } } },
    });
    usersApi.updateUser.mockResolvedValue({ id: 'u-e0071', is_active: false });
    render(<UserManager />);
    expect(await screen.findByText('E0071')).toBeInTheDocument();
    await userEvent.click(screen.getByTitle('Elimina'));
    await userEvent.click(screen.getByRole('button', { name: /^elimina$/i }));
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^elimina$/i })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /^disattiva$/i }));
    await waitFor(() => expect(usersApi.updateUser).toHaveBeenCalledWith('u-e0071', { is_active: false }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Utente "E0071" disattivato'));
  });

  // Eliminare mantenendo lo storico (10/11/2026): il superadmin, al 409,
  // ha anche «Elimina mantenendo lo storico»; un secondo passo spiega cosa
  // succede e chiede la conferma esplicita.
  it('utente con storico: il superadmin elimina mantenendo lo storico dopo la conferma', async () => {
    listUsersMock.mockResolvedValue([
      { id: 'u-e0071', username: 'E0071', role: 'esecutore', group_id: 'grp-1', is_active: true },
    ]);
    usersApi.deleteUser.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { error: 'user_has_history', message: 'storico', details: { references: [] } } },
    });
    usersApi.deleteUserKeepingHistory.mockResolvedValue({ success: true, id: 'u-e0071' });
    render(<UserManager />);
    expect(await screen.findByText('E0071')).toBeInTheDocument();
    await userEvent.click(screen.getByTitle('Elimina'));
    await userEvent.click(screen.getByRole('button', { name: /^elimina$/i }));
    expect(await screen.findByRole('button', { name: /^disattiva$/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /elimina mantenendo lo storico/i }));
    // Il secondo passo spiega e non ha ancora eliminato nulla.
    expect(await screen.findByText(/non potrà più accedere/)).toBeInTheDocument();
    expect(screen.getByText(/«utente eliminato \(nome\)»/)).toBeInTheDocument();
    expect(usersApi.deleteUserKeepingHistory).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: /conferma eliminazione/i }));
    await waitFor(() => expect(usersApi.deleteUserKeepingHistory).toHaveBeenCalledWith('u-e0071'));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Utente "E0071" eliminato, storico mantenuto'));
  });

  it('altro errore di eliminazione: nessun «Disattiva»', async () => {
    usersApi.deleteUser.mockRejectedValue({ isAxiosError: true, response: { status: 500, data: { error: 'internal_error' } } });
    render(<UserManager />);
    expect(await screen.findByText('mario')).toBeInTheDocument();
    await userEvent.click(screen.getByTitle('Elimina'));
    await userEvent.click(screen.getByRole('button', { name: /^elimina$/i }));
    await waitFor(() => expect(usersApi.deleteUser).toHaveBeenCalled());
    expect(await screen.findByText(/errore interno/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^disattiva$/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /elimina mantenendo lo storico/i })).toBeNull();
  });
});
