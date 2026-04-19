import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ScheduleWizard from '../../src/components/modals/ScheduleWizard';


const { apiIndexMock, schedulesMock, toastMock } = vi.hoisted(() => ({
  apiIndexMock: {
    listProjects: vi.fn(),
    listConnections: vi.fn(),
  },
  schedulesMock: {
    createSchedule: vi.fn(),
    updateSchedule: vi.fn(),
    getSchedule: vi.fn(),
  },
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock('../../src/api', () => apiIndexMock);
vi.mock('../../src/api/schedules', () => schedulesMock);
vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

describe('ScheduleWizard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('creates a schedule with the expected default payload shape', async () => {
    apiIndexMock.listProjects.mockResolvedValueOnce([
      { id: 'proj-1', name: 'Project One', project_type: 'estrai' },
    ]);
    apiIndexMock.listConnections.mockResolvedValueOnce([]);
    schedulesMock.createSchedule.mockResolvedValueOnce({ id: 'sch-new' });
    const onCreated = vi.fn();

    render(
      <ScheduleWizard
        onClose={vi.fn()}
        onCreated={onCreated}
      />,
    );

    expect((await screen.findAllByText('Project One')).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByText('Project One'));

    for (let i = 0; i < 5; i += 1) {
      await userEvent.click(screen.getByRole('button', { name: /avanti/i }));
    }
    await userEvent.click(screen.getByRole('button', { name: /crea schedule/i }));

    await waitFor(() => {
      expect(schedulesMock.createSchedule).toHaveBeenCalledTimes(1);
    });

    const [payload] = schedulesMock.createSchedule.mock.calls[0];
    expect(payload).toEqual({
      project_id: 'proj-1',
      schedule: {
        cron_expression: '0 3 * * *',
        description: '',
        timezone: 'Europe/Rome',
        enabled: true,
      },
      execution: {
        use_cache: false,
        concurrency_max_queue: 3,
      },
      destination: null,
      on_error: {
        max_retries: 3,
        retry_backoff_seconds: [60, 300, 900],
        append_conflicts: 'skip',
        log_skipped_rows: true,
      },
    });
    expect(toastMock.success).toHaveBeenCalled();
    expect(onCreated).toHaveBeenCalled();
  });

  it('loads an existing schedule and preserves nested update payload shape', async () => {
    apiIndexMock.listProjects.mockResolvedValueOnce([
      { id: 'proj-1', name: 'Project One', project_type: 'arricchisci' },
    ]);
    apiIndexMock.listConnections.mockResolvedValueOnce([
      { id: 'conn-1', name: 'Conn One', type: 'postgres' },
    ]);
    schedulesMock.getSchedule.mockResolvedValueOnce({
      pipeline: { project_id: 'proj-1' },
      schedule: {
        cron_expression: '0 3 * * *',
        description: 'Nightly sync',
        timezone: 'Europe/Rome',
      },
      execution: {
        use_cache: true,
      },
      destination: {
        connection_id: 'conn-1',
        schema_name: 'public',
        table_name: 'dest_table',
        write_mode: 'upsert',
        match_columns: ['id', 'code'],
        batch_size: 500,
        verify_destination_connection: false,
      },
      on_error: {
        max_retries: 2,
        retry_backoff_seconds: [60, 300],
        append_conflicts: 'force',
        log_skipped_rows: false,
      },
    });
    schedulesMock.updateSchedule.mockResolvedValueOnce({ id: 'sch-1' });
    const onCreated = vi.fn();

    render(
      <ScheduleWizard
        onClose={vi.fn()}
        onCreated={onCreated}
        editId="sch-1"
      />,
    );

    expect((await screen.findAllByText('Project One')).length).toBeGreaterThan(0);
    for (let i = 0; i < 5; i += 1) {
      await userEvent.click(screen.getByRole('button', { name: /avanti/i }));
    }
    await userEvent.click(screen.getByRole('button', { name: /salva modifiche|salva/i }));

    await waitFor(() => {
      expect(schedulesMock.updateSchedule).toHaveBeenCalledTimes(1);
    });

    const [scheduleId, payload] = schedulesMock.updateSchedule.mock.calls[0];
    expect(scheduleId).toBe('sch-1');
    expect(payload).toEqual({
      project_id: 'proj-1',
      schedule: {
        cron_expression: '0 3 * * *',
        description: 'Nightly sync',
        timezone: 'Europe/Rome',
        enabled: true,
      },
      execution: {
        use_cache: true,
        concurrency_max_queue: 3,
      },
      destination: {
        connection_id: 'conn-1',
        schema_name: 'public',
        table_name: 'dest_table',
        write_mode: 'upsert',
        match_columns: ['id', 'code'],
        batch_size: 500,
        verify_destination_connection: false,
      },
      on_error: {
        max_retries: 2,
        retry_backoff_seconds: [60, 300],
        append_conflicts: 'force',
        log_skipped_rows: false,
      },
    });
    expect(toastMock.success).toHaveBeenCalled();
    expect(onCreated).toHaveBeenCalled();
  });
});
