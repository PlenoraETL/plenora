import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ScheduleList from '../../src/pages/ScheduleList';

const { schedulesMock, toastMock, navigateMock, searchParamsMock } = vi.hoisted(() => ({
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
  searchParamsMock: globalThis.__frontendTestHelpers.createSearchParamsMock(vi),
}));

vi.mock('../../src/api/schedules', () => schedulesMock);
vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));
const authOverride = vi.hoisted(() => ({ user: null }));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => globalThis.__frontendTestHelpers.createAuthContextValue(vi, authOverride.user ? { user: authOverride.user } : {}),
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useSearchParams: () => searchParamsMock,
  };
});
vi.mock('../../src/utils/hexCanvas', () => ({
  useHexCanvas: () => {},
}));
vi.mock('../../src/components/AppSidebar', () => ({
  default: () => null,
}));
vi.mock('../../src/components/modals/ScheduleWizard', () => ({
  default: () => null,
}));

describe('ScheduleList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authOverride.user = null;
  });

  it('wires run-now and log expansion to the schedule API layer', async () => {
    schedulesMock.listSchedules
      .mockResolvedValueOnce({
        schedules: [
          {
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
              failure_count: 1,
              next_execution: '2026-04-16T03:00:00Z',
              last_execution: {
                timestamp: '2026-04-15T03:00:00Z',
                total_duration_ms: 4200,
                event: 'RUN_COMPLETED',
              },
            },
            destination: null,
          },
        ],
      })
      .mockResolvedValueOnce({
        schedules: [
          {
            id: 'sch-1',
            pipeline: { project_name: 'Nightly Import', project_type: 'estrai' },
            schedule: {
              enabled: true,
              description: 'Daily sync',
              cron_expression: '0 3 * * *',
            },
            runtime: {
              status: 'queued',
              total_runs: 8,
              failure_count: 1,
              next_execution: '2026-04-16T03:00:00Z',
              last_execution: {
                timestamp: '2026-04-15T03:00:00Z',
                total_duration_ms: 4200,
                event: 'RUN_COMPLETED',
              },
            },
            destination: null,
          },
        ],
      });
    schedulesMock.getBeatHealthcheck.mockResolvedValueOnce({ status: 'ok' });
    schedulesMock.runScheduleNow.mockResolvedValueOnce({ task_id: 'task-1' });
    schedulesMock.getScheduleLogs.mockResolvedValueOnce({
      logs: [
        {
          event: 'RUN_COMPLETED',
          timestamp: '2026-04-15T03:00:00Z',
          total_duration_ms: 4200,
        },
      ],
    });

    render(<ScheduleList />);

    await screen.findByText('Nightly Import');
    await userEvent.click(screen.getByRole('button', { name: /esegui ora/i }));

    await waitFor(() => {
      expect(schedulesMock.runScheduleNow).toHaveBeenCalledWith('sch-1');
    });
    await waitFor(() => {
      expect(schedulesMock.listSchedules).toHaveBeenCalledTimes(2);
    });

    await userEvent.click(screen.getByRole('button', { name: /log/i }));
    await waitFor(() => {
      expect(schedulesMock.getScheduleLogs).toHaveBeenCalledWith('sch-1', 1, 5);
    });
    expect(await screen.findByText('Ultime 5 esecuzioni')).toBeInTheDocument();
    expect(screen.getByText('RUN_COMPLETED')).toBeInTheDocument();
  });

  it('shows beat-down and running banners with the expected counters', async () => {
    schedulesMock.listSchedules.mockResolvedValueOnce({
      schedules: [
        {
          id: 'sch-running',
          pipeline: { project_name: 'Queued Job', project_type: 'arricchisci' },
          schedule: {
            enabled: true,
            description: 'Queued schedule',
            cron_expression: '*/15 * * * *',
          },
          runtime: {
            status: 'running',
            total_runs: 3,
            failure_count: 0,
            current_task_id: 'task-running-1',
          },
          destination: { table_name: 'dest_table', write_mode: 'replace' },
        },
      ],
    });
    schedulesMock.getBeatHealthcheck.mockResolvedValueOnce({
      status: 'down',
      message: 'Beat offline',
    });

    render(<ScheduleList />);

    expect(await screen.findByText(/pianificatore non è attivo/i)).toBeInTheDocument();
    expect(screen.getByText(/1 pianificazione in esecuzione/i)).toBeInTheDocument();
    expect(screen.getByText('Queued Job')).toBeInTheDocument();
    expect(screen.getByText('dest_table (replace)')).toBeInTheDocument();
  });

  it('a viewer sees no run, toggle, edit or delete actions (R10.1)', async () => {
    authOverride.user = { role: 'viewer', user_id: 'v1', username: 'viewer', group_id: 'grp-1' };
    schedulesMock.listSchedules.mockResolvedValueOnce({
      schedules: [{
        id: 'sch-1',
        pipeline: { project_name: 'Nightly Import', project_type: 'estrai' },
        schedule: { enabled: true, description: 'Daily sync', cron_expression: '0 3 * * *' },
        runtime: { status: 'completed', total_runs: 1, failure_count: 0 },
        destination: null,
      }],
    });
    schedulesMock.getBeatHealthcheck.mockResolvedValueOnce({ status: 'ok' });

    render(<ScheduleList />);
    await screen.findByText('Nightly Import');
    expect(screen.queryByRole('button', { name: /esegui ora/i })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: /attiva la pianificazione|disattiva la pianificazione/i })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Modifica schedule' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Elimina la pianificazione' })).toBeNull();
    expect(screen.getByRole('button', { name: /log/i })).toBeInTheDocument();
  });
});
