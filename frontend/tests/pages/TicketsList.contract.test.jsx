import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  navigateMock,
  toastMock,
  getTicketsMetaMock,
  listTicketsMock,
  createTicketMock,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
  getTicketsMetaMock: vi.fn(),
  listTicketsMock: vi.fn(),
  createTicketMock: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({
    user: { role: 'admin', username: 'admin-user', user_id: 'u-admin' },
    logout: vi.fn(),
  }),
}));

vi.mock('../../src/api/tickets', () => ({
  getTicketsMeta: getTicketsMetaMock,
  listTickets: listTicketsMock,
  createTicket: createTicketMock,
}));

// Sidebar contiene auth hooks, useAuth viene gia' mockato sopra: il
// componente monta cmq senza errori.

import { MemoryRouter } from 'react-router-dom';
import TicketsList from '../../src/pages/TicketsList';


const META = {
  kinds: ['tech_debt', 'user_support'],
  severities: ['low', 'medium', 'high', 'critical'],
  statuses: ['open', 'in_progress', 'resolved'],
  statuses_active: ['open', 'in_progress'],
  categories: [
    { value: 'auth', label: 'Autenticazione' },
    { value: 'pipeline', label: 'Pipeline' },
    { value: 'other', label: 'Altro' },
  ],
  allowed_attachment_extensions: ['png', 'log'],
  max_attachment_size_bytes: 10485760,
};

const TICKET_SAMPLE = {
  id: 'abc-123-def-456',
  kind: 'user_support',
  title: 'Problema login SSO',
  description: 'Non riesco ad accedere',
  severity: 'high',
  status: 'open',
  category: 'auth',
  created_by: 'u1',
  assignee: null,
  group_id: 'g1',
  created_at: '2026-04-18T10:00:00Z',
  updated_at: '2026-04-18T11:30:00Z',
  resolved_at: null,
};


describe('TicketsList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getTicketsMetaMock.mockResolvedValue(META);
    listTicketsMock.mockResolvedValue([TICKET_SAMPLE]);
  });

  it('fetcha meta + lista e render il ticket con badge', async () => {
    render(<MemoryRouter><TicketsList /></MemoryRouter>);

    await waitFor(() => {
      expect(getTicketsMetaMock).toHaveBeenCalled();
      expect(listTicketsMock).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(screen.getByText('Problema login SSO')).toBeInTheDocument();
    });

    // Badge severity "Alta" (mapping italiano) — sia chip filtro che cella
    expect(screen.getAllByText('Alta').length).toBeGreaterThan(0);
    // Badge kind "Supporto utente" appare nella cella del ticket
    const badges = screen.getAllByText('Supporto utente');
    expect(badges.length).toBeGreaterThan(0);
  });

  it('click su row naviga a /tickets/:id', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><TicketsList /></MemoryRouter>);

    await waitFor(() => screen.getByText('Problema login SSO'));
    await user.click(screen.getByText('Problema login SSO'));

    expect(navigateMock).toHaveBeenCalledWith('/tickets/abc-123-def-456');
  });

  it('applica filtro "solo i miei" alla chiamata API', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><TicketsList /></MemoryRouter>);

    await waitFor(() => screen.getByText('Problema login SSO'));
    listTicketsMock.mockClear();

    const mineCheckbox = screen.getByLabelText(/assegnati a me|solo i miei/i);
    await user.click(mineCheckbox);

    await waitFor(() => {
      expect(listTicketsMock).toHaveBeenCalledWith(
        expect.objectContaining({ mine: true }),
      );
    });
  });

  it('toggle status chip riflette filtro in chiamata API', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><TicketsList /></MemoryRouter>);

    await waitFor(() => screen.getByText('Problema login SSO'));
    listTicketsMock.mockClear();

    // Deseleziona "open" (era checked di default)
    const openChip = screen.getAllByLabelText(/open/i)[0];
    await user.click(openChip);

    await waitFor(() => {
      const call = listTicketsMock.mock.calls.at(-1)[0];
      expect(call.status).not.toContain('open');
    });
  });

  it('admin vede opzione "Debito tecnico" nel filtro kind', async () => {
    render(<MemoryRouter><TicketsList /></MemoryRouter>);

    await waitFor(() => screen.getByText('Problema login SSO'));
    expect(screen.getByRole('option', { name: 'Debito tecnico' })).toBeInTheDocument();
  });

  it('click su "Apri ticket" mostra il modal', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><TicketsList /></MemoryRouter>);

    await waitFor(() => screen.getByText('Problema login SSO'));
    await user.click(screen.getByRole('button', { name: /apri ticket/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Nuovo ticket')).toBeInTheDocument();
  });

  it('mostra messaggio vuoto quando non ci sono ticket', async () => {
    listTicketsMock.mockResolvedValue([]);
    render(<MemoryRouter><TicketsList /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText(/nessun ticket/i)).toBeInTheDocument();
    });
  });
});
