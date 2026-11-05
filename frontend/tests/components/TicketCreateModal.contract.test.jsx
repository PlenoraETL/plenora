import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  toastMock,
  createTicketMock,
} = vi.hoisted(() => ({
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
  createTicketMock: vi.fn(),
}));

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/api/tickets', () => ({
  createTicket: createTicketMock,
}));

import TicketCreateModal from '../../src/components/TicketCreateModal';


const META = {
  kinds: ['tech_debt', 'user_support'],
  severities: ['low', 'medium', 'high', 'critical'],
  statuses: ['open', 'in_progress', 'resolved'],
  categories: [
    { value: 'auth', label: 'Autenticazione' },
    { value: 'pipeline', label: 'Pipeline' },
    { value: 'other', label: 'Altro' },
  ],
};


describe('TicketCreateModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createTicketMock.mockResolvedValue({
      id: 'new-tk-1', title: 'Test', kind: 'user_support',
    });
  });

  // RBAC: opzione "Debito tecnico" visibile solo agli admin

  it('utente non-admin NON vede il dropdown Tipo', () => {
    render(
      <TicketCreateModal
        meta={META} user={{ role: 'user' }}
        onClose={() => {}} onCreated={() => {}}
      />,
    );
    // Solo i campi base (titolo, descrizione, severity, categoria)
    expect(screen.queryByText(/^tipo$/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: /debito tecnico/i }),
    ).not.toBeInTheDocument();
  });

  it('admin vede il dropdown Tipo con entrambi i kind', () => {
    render(
      <TicketCreateModal
        meta={META} user={{ role: 'admin' }}
        onClose={() => {}} onCreated={() => {}}
      />,
    );
    expect(screen.getByText(/^tipo$/i)).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /supporto utente/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /debito tecnico/i })).toBeInTheDocument();
  });

  it('superadmin vede il dropdown Tipo', () => {
    render(
      <TicketCreateModal
        meta={META} user={{ role: 'superadmin' }}
        onClose={() => {}} onCreated={() => {}}
      />,
    );
    expect(screen.getByRole('option', { name: /debito tecnico/i })).toBeInTheDocument();
  });

  // Validazione: titolo min 3 caratteri

  it('titolo < 3 caratteri → toast errore + niente API call', async () => {
    const user = userEvent.setup();
    render(
      <TicketCreateModal
        meta={META} user={{ role: 'user' }}
        onClose={() => {}} onCreated={() => {}}
      />,
    );

    const titleInput = screen.getByPlaceholderText(/riassunto breve/i);
    await user.type(titleInput, 'ab');
    // Submit programmaticamente perché HTML5 required blocca submit con stringa
    // di 2 char solo se vuoto. Forziamo via form.
    const form = titleInput.closest('form');
    form.requestSubmit();

    await waitFor(() => {
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringMatching(/almeno 3 caratteri/i),
      );
    });
    expect(createTicketMock).not.toHaveBeenCalled();
  });

  it('titolo solo whitespace → toast errore', async () => {
    const user = userEvent.setup();
    render(
      <TicketCreateModal
        meta={META} user={{ role: 'user' }}
        onClose={() => {}} onCreated={() => {}}
      />,
    );

    const titleInput = screen.getByPlaceholderText(/riassunto breve/i);
    await user.type(titleInput, '   ');
    const form = titleInput.closest('form');
    form.requestSubmit();

    await waitFor(() => {
      expect(toastMock.error).toHaveBeenCalled();
    });
    expect(createTicketMock).not.toHaveBeenCalled();
  });

  // Submit happy path

  it('submit valido chiama createTicket col payload e poi onCreated', async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    render(
      <TicketCreateModal
        meta={META} user={{ role: 'user' }}
        onClose={() => {}} onCreated={onCreated}
      />,
    );

    await user.type(
      screen.getByPlaceholderText(/riassunto breve/i),
      'Login non funziona',
    );
    await user.type(
      screen.getByPlaceholderText(/dettagli, passi/i),
      'Steps: 1. Apro app  2. Errore 500',
    );
    await user.click(screen.getByRole('button', { name: /apri ticket/i }));

    await waitFor(() => {
      expect(createTicketMock).toHaveBeenCalledWith({
        kind: 'user_support',
        title: 'Login non funziona',
        description: 'Steps: 1. Apro app  2. Errore 500',
        severity: 'medium',
        category: 'other',
      });
      expect(onCreated).toHaveBeenCalledWith({
        id: 'new-tk-1', title: 'Test', kind: 'user_support',
      });
    });
  });

  it('admin puo\' selezionare kind=tech_debt e il payload lo riflette', async () => {
    const user = userEvent.setup();
    render(
      <TicketCreateModal
        meta={META} user={{ role: 'admin' }}
        onClose={() => {}} onCreated={() => {}}
      />,
    );

    // Cambia kind a tech_debt (primo combobox)
    const kindSelect = screen.getAllByRole('combobox')[0];
    await user.selectOptions(kindSelect, 'tech_debt');

    await user.type(
      screen.getByPlaceholderText(/riassunto breve/i),
      'Refactor auth',
    );
    await user.click(screen.getByRole('button', { name: /apri ticket/i }));

    await waitFor(() => {
      expect(createTicketMock).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'tech_debt', title: 'Refactor auth' }),
      );
    });
  });

  it('errore API → toast errore + niente onCreated', async () => {
    createTicketMock.mockRejectedValue({
      message: 'Permesso negato',
      response: { data: { detail: 'Permesso negato' } },
    });
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(
      <TicketCreateModal
        meta={META} user={{ role: 'user' }}
        onClose={() => {}} onCreated={onCreated}
      />,
    );

    await user.type(
      screen.getByPlaceholderText(/riassunto breve/i),
      'Bug X',
    );
    await user.click(screen.getByRole('button', { name: /apri ticket/i }));

    await waitFor(() => {
      // toastApiError prefissa il contesto: 'Errore creazione ticket: <detail>'.
      expect(toastMock.error).toHaveBeenCalledWith('Errore creazione ticket: Permesso negato');
    });
    expect(onCreated).not.toHaveBeenCalled();
  });

  // Close

  it('click su X chiama onClose', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <TicketCreateModal
        meta={META} user={{ role: 'user' }}
        onClose={onClose} onCreated={() => {}}
      />,
    );

    // Il bottone X ha aria-label="Chiudi".
    await user.click(screen.getByRole('button', { name: /Chiudi/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it('click su Annulla chiama onClose', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <TicketCreateModal
        meta={META} user={{ role: 'user' }}
        onClose={onClose} onCreated={() => {}}
      />,
    );

    await user.click(screen.getByRole('button', { name: /annulla/i }));
    expect(onClose).toHaveBeenCalled();
  });

  // Severity / Category dropdowns popolati da meta

  it('le severity e categorie nel dropdown vengono da meta', () => {
    render(
      <TicketCreateModal
        meta={META} user={{ role: 'user' }}
        onClose={() => {}} onCreated={() => {}}
      />,
    );

    // Severity options (4 livelli)
    expect(screen.getByRole('option', { name: 'low' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'critical' })).toBeInTheDocument();
    // Categorie con label IT
    expect(screen.getByRole('option', { name: 'Autenticazione' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Pipeline' })).toBeInTheDocument();
  });

  it('senza meta usa fallback severity + categoria=Altro', () => {
    render(
      <TicketCreateModal
        meta={null} user={{ role: 'user' }}
        onClose={() => {}} onCreated={() => {}}
      />,
    );
    expect(screen.getByRole('option', { name: 'low' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Altro' })).toBeInTheDocument();
  });
});
