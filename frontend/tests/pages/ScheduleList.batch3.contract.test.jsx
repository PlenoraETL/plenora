import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ScheduleList from '../../src/pages/ScheduleList';

/**
 * Batch 3 di FE-TEST-001 su ScheduleList — gap ad alto valore non
 * coperti dal contract test esistente (ScheduleList.contract.test.jsx,
 * focalizzato su run-now + log expand + beat-down banner).
 *
 * Aree:
 *   1. delete: success path + reload, error path con toast formattato
 *   2. toggle enable/disable: success path + reload
 *   3. run-now error: cleanup di runningManual + toast
 *   4. runs panel: listScheduleRuns + download per-run
 *   5. permission gating: "+ Nuovo Schedule" nascosto per viewer
 *
 * Nessun refactor della pagina, nessun touch a useTheme/ThemeToggle,
 * nessuna modifica backend/docs/audits.
 *
 * `api/schedules.contract.test.js` copre gia' il boundary HTTP
 * (downloadScheduleOutput → blob, getScheduleLogs query params,
 * getDownloadUrl). I test qui sono page-level, non duplicano.
 */

const { schedulesMock, toastMock, navigateMock, searchParamsMock, authState } =
  vi.hoisted(() => ({
    schedulesMock: {
      listSchedules: vi.fn(),
      deleteSchedule: vi.fn(),
      toggleSchedule: vi.fn(),
      runScheduleNow: vi.fn(),
      getBeatHealthcheck: vi.fn(),
      getScheduleLogs: vi.fn(),
      listScheduleRuns: vi.fn(),
      downloadScheduleOutput: vi.fn(),
    },
    toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
    navigateMock: globalThis.__frontendTestHelpers.createNavigateMock(vi),
    searchParamsMock:
      globalThis.__frontendTestHelpers.createSearchParamsMock(vi),
    // Mutabile: i test possono cambiare role prima del render (default admin).
    authState: {
      user: { role: 'admin', username: 'tester', group_id: 'grp-1' },
    },
  }));

vi.mock('../../src/api/schedules', () => schedulesMock);
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({ user: authState.user, logout: vi.fn() }),
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useSearchParams: () => searchParamsMock,
  };
});
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));
vi.mock('../../src/components/modals/ScheduleWizard', () => ({
  default: () => null,
}));

const sampleSchedule = {
  id: 'sch-1',
  pipeline: { project_name: 'Nightly Import', project_type: 'estrai' },
  schedule: {
    enabled: true,
    description: 'Daily sync',
    cron_expression: '0 3 * * *',
  },
  runtime: {
    status: 'completed',
    total_runs: 8,
    failure_count: 0,
    next_execution: '2026-04-16T03:00:00Z',
    last_execution: {
      timestamp: '2026-04-15T03:00:00Z',
      total_duration_ms: 4200,
      event: 'RUN_COMPLETED',
    },
  },
  destination: null,
};

describe('ScheduleList — batch 3', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'admin', username: 'tester', group_id: 'grp-1' };
    schedulesMock.getBeatHealthcheck.mockResolvedValue({ status: 'ok' });
  });

  // ── 1a. Delete success ────────────────────────────────────────────────

  it('deletes a schedule after confirm and reloads the list', async () => {
    schedulesMock.listSchedules
      .mockResolvedValueOnce({ schedules: [sampleSchedule] })
      .mockResolvedValueOnce({ schedules: [] });
    schedulesMock.deleteSchedule.mockResolvedValueOnce({ ok: true });

    render(<ScheduleList />);
    await screen.findByText('Nightly Import');

    await userEvent.click(screen.getByRole('button', { name: /Elimina schedule/i }));
    // ConfirmDialog button has text "Elimina" (confirmText nel componente).
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() =>
      expect(schedulesMock.deleteSchedule).toHaveBeenCalledWith('sch-1'),
    );
    await waitFor(() =>
      expect(schedulesMock.listSchedules).toHaveBeenCalledTimes(2),
    );
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  // ── 1b. Delete error ──────────────────────────────────────────────────

  it('surfaces backend detail message via toast on delete error', async () => {
    schedulesMock.listSchedules.mockResolvedValueOnce({
      schedules: [sampleSchedule],
    });
    schedulesMock.deleteSchedule.mockRejectedValueOnce({
      response: { data: { detail: 'lock owned by other worker' } },
      message: 'Request failed',
    });

    render(<ScheduleList />);
    await screen.findByText('Nightly Import');

    await userEvent.click(screen.getByRole('button', { name: /Elimina schedule/i }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    const [msg] = toastMock.error.mock.calls[0];
    expect(msg).toContain('Errore eliminazione');
    expect(msg).toContain('lock owned by other worker');
    // Reload non scatta dopo errore (deleteSchedule throw → loadData skipped).
    expect(schedulesMock.listSchedules).toHaveBeenCalledTimes(1);
  });

  // ── 2. Toggle enable/disable ─────────────────────────────────────────

  it('toggles a schedule from enabled to disabled and reloads', async () => {
    schedulesMock.listSchedules
      .mockResolvedValueOnce({ schedules: [sampleSchedule] })
      .mockResolvedValueOnce({
        schedules: [
          {
            ...sampleSchedule,
            schedule: { ...sampleSchedule.schedule, enabled: false },
          },
        ],
      });
    schedulesMock.toggleSchedule.mockResolvedValueOnce({ ok: true });

    render(<ScheduleList />);
    await screen.findByText('Nightly Import');

    // Toggle slider e' un input checkbox dentro <label className="sl__toggle">.
    const toggleInput = screen
      .getAllByRole('checkbox')
      .find((el) => el.checked);
    expect(toggleInput).toBeTruthy();
    await userEvent.click(toggleInput);

    await waitFor(() =>
      // current=true → toggleSchedule(id, !current) === toggleSchedule(id, false)
      expect(schedulesMock.toggleSchedule).toHaveBeenCalledWith('sch-1', false),
    );
    await waitFor(() =>
      expect(schedulesMock.listSchedules).toHaveBeenCalledTimes(2),
    );
  });

  // ── 3. Run Now error path ────────────────────────────────────────────

  it('clears runningManual and toasts when runScheduleNow rejects', async () => {
    schedulesMock.listSchedules.mockResolvedValueOnce({
      schedules: [sampleSchedule],
    });
    schedulesMock.runScheduleNow.mockRejectedValueOnce({
      response: { data: { detail: 'queue full' } },
      message: 'Request failed',
    });

    render(<ScheduleList />);
    await screen.findByText('Nightly Import');

    const runBtn = screen.getByRole('button', { name: /esegui ora/i });
    await userEvent.click(runBtn);

    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    const [msg] = toastMock.error.mock.calls[0];
    expect(msg).toContain('queue full');

    // Cleanup: il bottone torna disponibile (nessun "⏳ In corso..." sticky).
    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: /in corso/i }),
      ).not.toBeInTheDocument();
    });
    expect(
      screen.getByRole('button', { name: /esegui ora/i }),
    ).toBeInTheDocument();
  });

  // ── 4. Runs panel + per-run download ─────────────────────────────────

  it('expands the runs panel and downloads a specific archived run', async () => {
    schedulesMock.listSchedules.mockResolvedValueOnce({
      schedules: [sampleSchedule],
    });
    schedulesMock.listScheduleRuns.mockResolvedValueOnce({
      runs: [
        {
          // formatRunTimestamp richiede length >= 15 (YYYYMMDDTHHMMSS).
          timestamp: '20260415T030000',
          files: [{ name: 'output.csv', size_bytes: 2048 }],
        },
      ],
    });

    render(<ScheduleList />);
    await screen.findByText('Nightly Import');

    // Bottone "📥 Download" e' visibile solo se schedule.runtime.last_execution
    // e' valorizzato (sampleSchedule lo ha).
    await userEvent.click(screen.getByRole('button', { name: /📥 Download/ }));

    await waitFor(() =>
      expect(schedulesMock.listScheduleRuns).toHaveBeenCalledWith('sch-1'),
    );
    expect(await screen.findByText('output.csv')).toBeInTheDocument();
    expect(screen.getByText(/Storico esecuzioni/i)).toBeInTheDocument();

    // Click sul bottone download del run specifico (l'unico "📥" rimasto
    // dopo che il toggle ha cambiato testo in "📥 Chiudi" — il run entry
    // ha solo l'icona).
    const runDownloadBtn = screen.getByRole('button', {
      name: /Scarica output di questa esecuzione/i,
    });
    await userEvent.click(runDownloadBtn);

    expect(schedulesMock.downloadScheduleOutput).toHaveBeenCalledWith(
      'sch-1',
      '20260415T030000',
    );
  });

  // ── 5. Permission gating: viewer non vede "+ Nuovo Schedule" ─────────

  it('hides the "+ Nuovo Schedule" button for viewer role', async () => {
    authState.user = { role: 'viewer', username: 'v', group_id: 'grp-1' };
    schedulesMock.listSchedules.mockResolvedValueOnce({
      schedules: [sampleSchedule],
    });

    render(<ScheduleList />);
    await screen.findByText('Nightly Import');

    // can.createSchedule = isAdminOrAbove → viewer NON puo' creare.
    expect(
      screen.queryByRole('button', { name: /Nuovo Schedule/i }),
    ).not.toBeInTheDocument();
    // Sanity: un admin invece la vede (testato implicitamente dagli altri
    // test di questo file).
  });
});
