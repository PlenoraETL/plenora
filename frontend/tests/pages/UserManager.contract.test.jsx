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
});
