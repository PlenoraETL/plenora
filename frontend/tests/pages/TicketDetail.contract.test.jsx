import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  navigateMock,
  toastMock,
  getTicketMock,
  getTicketsMetaMock,
  updateTicketMock,
  addCommentMock,
  uploadAttachmentMock,
  deleteAttachmentMock,
  openAttachmentMock,
  deleteTicketMock,
  authState,
  paramsState,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
  getTicketMock: vi.fn(),
  getTicketsMetaMock: vi.fn(),
  updateTicketMock: vi.fn(),
  addCommentMock: vi.fn(),
  uploadAttachmentMock: vi.fn(),
  deleteAttachmentMock: vi.fn(),
  openAttachmentMock: vi.fn(),
  deleteTicketMock: vi.fn(),
  authState: { user: null },
  paramsState: { id: 'tk-1' },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useParams: () => ({ id: paramsState.id }),
  };
});

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => authState,
}));

vi.mock('../../src/api/tickets', () => ({
  getTicket: getTicketMock,
  getTicketsMeta: getTicketsMetaMock,
  updateTicket: updateTicketMock,
  addComment: addCommentMock,
  uploadAttachment: uploadAttachmentMock,
  deleteAttachment: deleteAttachmentMock,
  openAttachment: openAttachmentMock,
  deleteTicket: deleteTicketMock,
}));

import { MemoryRouter } from 'react-router-dom';
import TicketDetail from '../../src/pages/TicketDetail';


const META = {
  kinds: ['tech_debt', 'user_support'],
  severities: ['low', 'medium', 'high', 'critical'],
  statuses: ['open', 'in_progress', 'waiting_user', 'resolved', 'closed', 'reopened'],
  statuses_active: ['open', 'in_progress', 'waiting_user', 'reopened'],
  categories: [
    { value: 'auth', label: 'Autenticazione' },
    { value: 'other', label: 'Altro' },
  ],
  allowed_attachment_extensions: ['png', 'jpg', 'log', 'txt'],
  max_attachment_size_bytes: 10485760,
};

const BASE_TICKET = {
  id: 'tk-1',
  kind: 'user_support',
  title: 'SSO non funziona',
  description: 'Login fallisce con errore 500',
  severity: 'high',
  status: 'open',
  category: 'auth',
  created_by: 'u-creator',
  assignee: null,
  group_id: 'g1',
  created_at: '2026-04-18T10:00:00Z',
  updated_at: '2026-04-18T11:30:00Z',
  resolved_at: null,
};

const BASE_DETAIL = {
  ticket: BASE_TICKET,
  comments: [],
  attachments: [],
};

function renderDetail() {
  return render(<MemoryRouter><TicketDetail /></MemoryRouter>);
}


describe('TicketDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    paramsState.id = 'tk-1';
    authState.user = { user_id: 'u-admin', username: 'admin1', role: 'admin' };
    getTicketsMetaMock.mockResolvedValue(META);
    getTicketMock.mockResolvedValue(BASE_DETAIL);
    updateTicketMock.mockResolvedValue(BASE_TICKET);
    addCommentMock.mockResolvedValue({ id: 'c-1' });
    uploadAttachmentMock.mockResolvedValue({ id: 'att-1' });
    deleteAttachmentMock.mockResolvedValue({ ok: true });
    openAttachmentMock.mockResolvedValue(undefined);
    deleteTicketMock.mockResolvedValue({ ok: true });
  });

  // RENDER

  it('mostra titolo, descrizione e id corto', async () => {
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));
    expect(screen.getByText('Login fallisce con errore 500')).toBeInTheDocument();
    // id corto = primi 8 char
    expect(screen.getByText(/#tk-1/)).toBeInTheDocument();
  });

  it('admin vede select per cambiare status, severity, categoria', async () => {
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));
    // 3 select: status, severity, categoria
    const selects = screen.getAllByRole('combobox');
    expect(selects.length).toBeGreaterThanOrEqual(3);
  });

  // STATUS / SEVERITY / CATEGORY change (admin)

  it('admin cambia status → chiama updateTicket con nuovo status', async () => {
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));

    // Il primo select e' status
    const statusSelect = screen.getAllByRole('combobox')[0];
    await user.selectOptions(statusSelect, 'in_progress');

    await waitFor(() => {
      expect(updateTicketMock).toHaveBeenCalledWith('tk-1', { status: 'in_progress' });
    });
  });

  it('admin cambia severity → chiama updateTicket', async () => {
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));

    const severitySelect = screen.getAllByRole('combobox')[1];
    await user.selectOptions(severitySelect, 'critical');

    await waitFor(() => {
      expect(updateTicketMock).toHaveBeenCalledWith('tk-1', { severity: 'critical' });
    });
  });

  // RIAPRI flow (creator non-admin su ticket resolved)

  it('creator non-admin vede pulsante "Riapri" su ticket resolved', async () => {
    authState.user = { user_id: 'u-creator', username: 'creator', role: 'user' };
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      ticket: { ...BASE_TICKET, status: 'resolved', created_by: 'u-creator' },
    });
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));
    expect(screen.getByRole('button', { name: /riapri/i })).toBeInTheDocument();
  });

  it('click "Riapri" chiama updateTicket con status=reopened', async () => {
    authState.user = { user_id: 'u-creator', username: 'creator', role: 'user' };
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      ticket: { ...BASE_TICKET, status: 'closed', created_by: 'u-creator' },
    });
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));

    await user.click(screen.getByRole('button', { name: /riapri/i }));

    await waitFor(() => {
      expect(updateTicketMock).toHaveBeenCalledWith('tk-1', { status: 'reopened' });
    });
  });

  it('user non-creator NON vede pulsante "Riapri"', async () => {
    authState.user = { user_id: 'u-other', username: 'other', role: 'user' };
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      ticket: { ...BASE_TICKET, status: 'resolved', created_by: 'u-creator' },
    });
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));
    expect(screen.queryByRole('button', { name: /riapri/i })).not.toBeInTheDocument();
  });

  it('user creator NON vede "Riapri" se ticket e\' open', async () => {
    authState.user = { user_id: 'u-creator', username: 'creator', role: 'user' };
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      ticket: { ...BASE_TICKET, status: 'open', created_by: 'u-creator' },
    });
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));
    expect(screen.queryByRole('button', { name: /riapri/i })).not.toBeInTheDocument();
  });

  // COMMENTS

  it('mostra commenti esistenti con autore e data', async () => {
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      comments: [
        {
          id: 'c-1', author_username: 'mario', body: 'Sto investigando',
          created_at: '2026-04-18T12:00:00Z',
        },
      ],
    });
    renderDetail();
    await waitFor(() => screen.getByText('Sto investigando'));
    expect(screen.getByText('mario')).toBeInTheDocument();
  });

  it('submit form commento chiama addComment e svuota textarea', async () => {
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));

    const textarea = screen.getByPlaceholderText(/scrivi un commento/i);
    await user.type(textarea, 'Ho fatto delle prove');
    await user.click(screen.getByRole('button', { name: /invia/i }));

    await waitFor(() => {
      expect(addCommentMock).toHaveBeenCalledWith('tk-1', 'Ho fatto delle prove');
    });
  });

  // ATTACHMENTS

  it('mostra allegati esistenti con nome, size e uploader', async () => {
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      attachments: [
        {
          id: 'att-1', original_filename: 'screenshot.png',
          size_bytes: 51200, uploaded_at: '2026-04-18T13:00:00Z',
          uploaded_by_username: 'mario',
        },
      ],
    });
    renderDetail();
    await waitFor(() => screen.getByText('screenshot.png'));
    // size dal formattatore unico (utils/format.formatBytes)
    expect(screen.getByText(/50 KB/)).toBeInTheDocument();
    expect(screen.getByText(/mario/)).toBeInTheDocument();
  });

  it('upload file chiama uploadAttachment con file', async () => {
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));

    const fileInput = document.querySelector('input[type="file"]');
    expect(fileInput).toBeTruthy();

    const file = new File(['fakedata'], 'log.log', { type: 'text/plain' });
    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(uploadAttachmentMock).toHaveBeenCalledWith('tk-1', file);
    });
  });

  it('click sul filename chiama openAttachment con ticketId, aid, filename', async () => {
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      attachments: [
        {
          id: 'att-1', original_filename: 'screenshot.png',
          size_bytes: 2048, uploaded_at: '2026-04-18T13:00:00Z',
          uploaded_by_username: 'mario',
        },
      ],
    });
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('screenshot.png'));

    await user.click(screen.getByText('screenshot.png'));

    await waitFor(() => {
      expect(openAttachmentMock).toHaveBeenCalledWith(
        'tk-1', 'att-1', 'screenshot.png',
      );
    });
  });

  it('errore su openAttachment mostra toast', async () => {
    openAttachmentMock.mockRejectedValue({
      message: 'Blob non disponibile',
      response: { data: { detail: 'Blob non disponibile' } },
    });
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      attachments: [
        {
          id: 'att-1', original_filename: 'log.log',
          size_bytes: 100, uploaded_at: '2026-04-18T13:00:00Z',
          uploaded_by_username: 'mario',
        },
      ],
    });
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('log.log'));

    await user.click(screen.getByText('log.log'));

    await waitFor(() => {
      // toastApiError prefissa il contesto: 'Errore apertura allegato: <detail>'.
      expect(toastMock.error).toHaveBeenCalledWith('Errore apertura allegato: Blob non disponibile');
    });
  });

  it('click "Rimuovi" su allegato chiama deleteAttachment dopo conferma', async () => {
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      attachments: [
        {
          id: 'att-1', original_filename: 'log.log',
          size_bytes: 100, uploaded_at: '2026-04-18T13:00:00Z',
          uploaded_by_username: 'mario',
        },
      ],
    });
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('log.log'));

    // Apre il modal cliccando "Rimuovi" sull'allegato
    await user.click(screen.getByRole('button', { name: /rimuovi/i }));

    // Conferma cliccando "Rimuovi" nel ConfirmDialog (title "Rimuovi
    // allegato"). Dopo apertura ci sono due button con nome "Rimuovi":
    // quello dell'allegato (lista) + quello di conferma (dialog).
    // Prendiamo il secondo, che e' nel ConfirmDialog.
    const removeButtons = await screen.findAllByRole('button', { name: /^rimuovi$/i });
    await user.click(removeButtons[removeButtons.length - 1]);

    await waitFor(() => {
      expect(deleteAttachmentMock).toHaveBeenCalledWith('tk-1', 'att-1');
    });
  });

  it('click "Rimuovi" annullato dalla conferma NON chiama deleteAttachment', async () => {
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      attachments: [
        {
          id: 'att-1', original_filename: 'log.log',
          size_bytes: 100, uploaded_at: '2026-04-18T13:00:00Z',
          uploaded_by_username: 'mario',
        },
      ],
    });
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('log.log'));

    // Apre il modal
    await user.click(screen.getByRole('button', { name: /rimuovi/i }));

    // Annulla cliccando "Annulla" nel ConfirmDialog
    const cancelBtn = await screen.findByRole('button', { name: /annulla/i });
    await user.click(cancelBtn);

    expect(deleteAttachmentMock).not.toHaveBeenCalled();
  });

  // DELETE TICKET

  it('admin vede pulsante "Elimina ticket"', async () => {
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));
    expect(screen.getByRole('button', { name: /elimina ticket/i })).toBeInTheDocument();
  });

  it('superadmin vede pulsante "Elimina ticket"', async () => {
    authState.user = { user_id: 'sa', username: 'sa', role: 'superadmin' };
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));
    expect(screen.getByRole('button', { name: /elimina ticket/i })).toBeInTheDocument();
  });

  it('user NON vede pulsante "Elimina ticket"', async () => {
    authState.user = { user_id: 'u-creator', username: 'creator', role: 'user' };
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      ticket: { ...BASE_TICKET, created_by: 'u-creator' },
    });
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));
    expect(screen.queryByRole('button', { name: /elimina ticket/i })).not.toBeInTheDocument();
  });

  it('click "Elimina" dopo confirm chiama deleteTicket e naviga a /tickets', async () => {
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));

    // Apre il modal
    await user.click(screen.getByRole('button', { name: /elimina ticket/i }));

    // Conferma nel ConfirmDialog (title "Elimina ticket", confirm
    // button label "Elimina"). Cerchiamo specificamente "Elimina" senza
    // " ticket" — match esatto distingue dalla toolbar.
    const confirmBtn = await screen.findByRole('button', { name: /^elimina$/i });
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(deleteTicketMock).toHaveBeenCalledWith('tk-1');
      expect(navigateMock).toHaveBeenCalledWith('/tickets');
    });
  });

  it('click "Elimina" annullato dalla conferma NON chiama API', async () => {
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));

    await user.click(screen.getByRole('button', { name: /elimina ticket/i }));

    // Annulla nel ConfirmDialog
    const cancelBtn = await screen.findByRole('button', { name: /annulla/i });
    await user.click(cancelBtn);

    expect(deleteTicketMock).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('errore su deleteTicket mostra toast e NON naviga', async () => {
    deleteTicketMock.mockRejectedValue({
      message: 'Permesso negato',
      response: { data: { detail: 'Permesso negato' } },
    });
    const user = userEvent.setup();
    renderDetail();
    await waitFor(() => screen.getByText('SSO non funziona'));

    await user.click(screen.getByRole('button', { name: /elimina ticket/i }));

    const confirmBtn = await screen.findByRole('button', { name: /^elimina$/i });
    await user.click(confirmBtn);

    await waitFor(() => {
      // toastApiError prefissa il contesto: 'Errore eliminazione ticket: <detail>'.
      expect(toastMock.error).toHaveBeenCalledWith('Errore eliminazione ticket: Permesso negato');
    });
    expect(navigateMock).not.toHaveBeenCalled();
  });

  // ERROR HANDLING

  // Fase 5 T2 (R4.1, R4.8): errori come errori, dati per ticket.
  it('404: "Ticket non trovato" con ritorno ai ticket, non una pagina bianca', async () => {
    getTicketMock.mockRejectedValue({ response: { status: 404, data: { detail: 'Not found' } } });
    renderDetail();
    expect(await screen.findByText('Ticket non trovato')).toBeInTheDocument();
    // Ritorno alla lista: link alla pagina madre (PageLayout `parent`), non la storia.
    expect(screen.getByRole('link', { name: '← Ticket' })).toHaveAttribute('href', '/tickets');
  });

  it('errore diverso da 404: messaggio e Riprova che ricarica', async () => {
    getTicketMock.mockRejectedValueOnce({ response: { status: 500, data: {} } });
    renderDetail();
    expect(await screen.findByText('Ticket non caricato')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Riprova' }));
    expect(await screen.findByText('SSO non funziona')).toBeInTheDocument();
  });

  it('cambio di ticket: la risposta di A arrivata dopo non compare sotto B', async () => {
    let resolveA;
    getTicketMock.mockImplementation((tid) => (tid === 'tk-1'
      ? new Promise((r) => { resolveA = r; })
      : Promise.resolve({ ...BASE_DETAIL, ticket: { ...BASE_TICKET, id: 'tk-2', title: 'Ticket B' } })));
    const view = renderDetail();
    paramsState.id = 'tk-2';
    view.rerender(<MemoryRouter><TicketDetail /></MemoryRouter>);
    expect(await screen.findByText('Ticket B')).toBeInTheDocument();
    resolveA(BASE_DETAIL);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText('SSO non funziona')).toBeNull();
    expect(screen.getByText('Ticket B')).toBeInTheDocument();
  });

  it('il testo scritto durante l\'invio del commento resta', async () => {
    let finish;
    addCommentMock.mockImplementation(() => new Promise((r) => { finish = r; }));
    renderDetail();
    const textarea = await screen.findByPlaceholderText(/scrivi un commento/i);
    await userEvent.type(textarea, 'primo');
    await userEvent.click(screen.getByRole('button', { name: /invia/i }));
    await userEvent.type(textarea, ' e altro');
    finish({ id: 'c-1' });
    await waitFor(() => expect(getTicketMock).toHaveBeenCalledTimes(2));
    expect(textarea).toHaveValue('primo e altro');
  });

  it('Rimuovi allegato: solo admin o chi lo ha caricato (R10.9)', async () => {
    getTicketMock.mockResolvedValue({
      ...BASE_DETAIL,
      attachments: [
        { id: 'att-mio', original_filename: 'mio.png', size_bytes: 1, uploaded_at: '2026-04-18T13:00:00Z', uploaded_by: 'u-other', uploaded_by_username: 'other' },
        { id: 'att-altrui', original_filename: 'altrui.png', size_bytes: 1, uploaded_at: '2026-04-18T13:00:00Z', uploaded_by: 'u-creator', uploaded_by_username: 'creator' },
      ],
    });
    authState.user = { user_id: 'u-other', username: 'other', role: 'user' };
    renderDetail();
    await waitFor(() => screen.getByText('mio.png'));
    expect(screen.getAllByRole('button', { name: 'Rimuovi' })).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Elimina ticket' })).toBeNull();
  });
});
