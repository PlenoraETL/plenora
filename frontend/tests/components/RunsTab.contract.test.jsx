import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import RunsTab from '../../src/components/logviewer/RunsTab';

/**
 * Contract tests for the LogViewer RunsTab subcomponent.
 *
 * Covers table rendering, status filtering, stat-card filtering, pagination,
 * and load-error behavior.
 */

const { getLogRunsMock, toastMock } = vi.hoisted(() => ({
  getLogRunsMock: vi.fn(),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
}));

vi.mock('../../src/api/logRuns', () => ({
  getLogRuns: getLogRunsMock,
}));
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
// Stub RunDetail so these tests stay focused on RunsTab behavior.
vi.mock('../../src/components/logviewer/RunDetail', () => ({
  default: () => null,
}));

const sampleRuns = [
  {
    run_id: 'run-1',
    ts_start: '2026-04-15T10:00:00.000Z',
    status: 'success',
    module: 'estrai',
    project_id: 'proj-very-long-id-here',
    duration_ms: 4200,
    rows_out: 1234,
    triggered_by: 'manual',
  },
  {
    run_id: 'run-2',
    ts_start: '2026-04-15T11:00:00.000Z',
    status: 'failure',
    module: 'arricchisci',
    project_id: 'proj-2',
    duration_ms: 2100,
    rows_out: 0,
    triggered_by: 'cron',
    error_message: 'API quota exceeded',
  },
];

function lastRunsParams() {
  const calls = getLogRunsMock.mock.calls;
  return calls[calls.length - 1]?.[0] || {};
}

describe('RunsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getLogRunsMock.mockResolvedValue({
      runs: sampleRuns,
      total: 2,
      has_more: false,
    });
  });


  it('loads and renders run rows with status + trigger badges', async () => {
    render(<RunsTab />);

    // Aspetta che la tabella sia popolata. I trigger labels (`manual`,
    // `cron`) sono unici per riga; `Fallita` (status singolare nel badge)
    // e' distinta da `Fallite` (label dropdown). I nomi modulo
    // (`estrai`/`arricchisci`) ricorrono anche nei dropdown filter, e
    // `Successo` ricorre come option select, quindi non sono affidabili
    // come marker di riga.
    expect(await screen.findByText('manual')).toBeInTheDocument();
    expect(screen.getByText('cron')).toBeInTheDocument();
    expect(screen.getByText('Fallita')).toBeInTheDocument();

    // Mount-effect ha chiamato getLogRuns(0) con limit=PAGE.
    const params = lastRunsParams();
    expect(params).toMatchObject({ limit: 30, offset: 0 });
    expect(params.status).toBeUndefined();
    expect(params.module).toBeUndefined();
  });


  it('changes the status filter via select and reloads with the status param', async () => {
    render(<RunsTab />);
    await screen.findByText('estrai');

    const allStatusOption = screen.getByRole('option', {
      name: /Tutti gli stati/i,
    });
    const statusSelect = allStatusOption.closest('select');
    expect(statusSelect).toBeTruthy();

    await userEvent.selectOptions(statusSelect, 'failure');

    await waitFor(() => {
      expect(lastRunsParams().status).toBe('failure');
    });
  });


  it('clicking the "Completate" stat card toggles filterStatus="success" and reloads', async () => {
    render(<RunsTab />);
    await screen.findByText('estrai');

    const statCard = (await screen.findByText('Completate')).closest('.lv__stat');
    expect(statCard).toBeTruthy();

    await userEvent.click(statCard);

    await waitFor(() => {
      expect(lastRunsParams().status).toBe('success');
    });
    // Active class applicata dopo il click.
    expect(statCard.classList.contains('lv__stat--active')).toBe(true);
  });


  it('clicks "Successivi →" and reloads with the next offset (PAGE=30)', async () => {
    getLogRunsMock.mockResolvedValueOnce({
      runs: sampleRuns,
      total: 75, // > PAGE → pagination row visibile
      has_more: true, // → bottone Successivi abilitato
    });

    render(<RunsTab />);
    await screen.findByText('estrai');

    const nextBtn = await screen.findByRole('button', {
      name: /Successivi/i,
    });
    expect(nextBtn).not.toBeDisabled();

    await userEvent.click(nextBtn);

    await waitFor(() => {
      expect(lastRunsParams().offset).toBe(30);
    });
  });


  it('expands a failure row on click showing the error_message banner', async () => {
    render(<RunsTab />);
    await screen.findByText('cron');

    // Riga "cron" e' run-2 (status='failure', error_message='API quota exceeded').
    const row = screen.getByText('cron').closest('tr');
    await userEvent.click(row);

    // Banner inline visibile con il messaggio dell'errore.
    expect(
      await screen.findByText('API quota exceeded'),
    ).toBeInTheDocument();
  });

  // Fase 5 T2 (R4.8): un errore e' un errore con «Riprova», mai la tabella vuota.
  it('getLogRuns rifiuta: errore con il messaggio e Riprova, non "Nessuna esecuzione"', async () => {
    getLogRunsMock.mockReset();
    getLogRunsMock.mockRejectedValueOnce(new Error('redis offline'));
    getLogRunsMock.mockResolvedValue({ runs: [], total: 0, has_more: false });

    render(<RunsTab />);

    expect(await screen.findByText('Esecuzioni non caricate')).toBeInTheDocument();
    expect(screen.getByText('redis offline')).toBeInTheDocument();
    expect(screen.queryByText('Nessuna esecuzione trovata')).toBeNull();
    expect(toastMock.error).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Riprova' }));
    expect(await screen.findByText('Nessuna esecuzione trovata')).toBeInTheDocument();
  });
});
