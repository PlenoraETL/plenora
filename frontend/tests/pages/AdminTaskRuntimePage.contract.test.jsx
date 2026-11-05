import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AdminTaskRuntimePage from '../../src/pages/AdminTaskRuntimePage';

const { getTaskRuntimeMock, resolveUncertainCaricaRunMock } = vi.hoisted(() => ({
  getTaskRuntimeMock: vi.fn(),
  resolveUncertainCaricaRunMock: vi.fn(),
}));

vi.mock('../../src/api/adminTaskRuntime', () => ({
  getTaskRuntime: getTaskRuntimeMock,
  resolveUncertainCaricaRun: resolveUncertainCaricaRunMock,
}));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));
const authState = vi.hoisted(() => ({ user: { role: 'superadmin', user_id: 's' } }));
vi.mock('../../src/context/AuthContext', () => ({ useAuth: () => authState }));

describe('AdminTaskRuntimePage contract', () => {
  beforeEach(() => {
    getTaskRuntimeMock.mockResolvedValue({
      status: 'warn',
      broker_message: 'RabbitMQ raggiungibile',
      warnings: [],
      totals: {
        messages_ready: 3,
        running_tasks: 1,
        workers_online: 2,
        consumers: 3,
        expired_task_leases: 1,
        dead_letters: 0,
      },
      queues: [{
        name: 'data-plane', kind: 'work', messages_ready: 3,
        consumers: 2, status: 'ok', exists: true,
      }],
      workers: [{
        worker_id: 'worker-1', role: 'data-plane', queues: ['data-plane'],
        hostname: 'host-a', pid: 123, heartbeat_age_seconds: 2,
        status: 'online',
      }],
      running_tasks: [{
        task_id: 'task-123', actor_name: 'pipeline.execute',
        worker_id: 'worker-1', elapsed_seconds: 42, progress: 50,
        message: 'Elaborazione', status: 'running',
      }],
      uncertain_runs: [],
    });
    resolveUncertainCaricaRunMock.mockResolvedValue({ status: 'completed' });
  });

  afterEach(() => vi.clearAllMocks());

  it('shows queue, worker, and running task telemetry', async () => {
    render(<AdminTaskRuntimePage />);

    expect((await screen.findAllByText('data-plane')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('worker-1').length).toBeGreaterThan(0);
    expect(screen.getByText('task-123')).toBeInTheDocument();
    expect(screen.getByText('Elaborazione')).toBeInTheDocument();
    expect(getTaskRuntimeMock).toHaveBeenCalledTimes(1);
  });

  const UNCERTAIN = {
    status: 'warn', warnings: [], queues: [], workers: [], running_tasks: [],
    totals: { uncertain_carica_runs: 1 },
    uncertain_runs: [{
      run_id: 'run-uncertain-1', project_id: 'project-1', group_id: 'group-1',
      timestamp: '2026-07-21T08:00:00Z', error_message: 'Esito incerto',
    }],
  };

  it('resolves uncertain CARICA runs from a project dialog, only after an operator note', async () => {
    getTaskRuntimeMock.mockResolvedValue(UNCERTAIN);
    const promptSpy = vi.spyOn(window, 'prompt');

    render(<AdminTaskRuntimePage />);
    expect(await screen.findByText('run-uncertain-1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Autorizza retry' }));

    // Il dialog spiega le conseguenze e non conferma senza nota.
    expect(screen.getByText(/scritti una seconda volta/)).toBeInTheDocument();
    const dialogButtons = screen.getAllByRole('button', { name: 'Autorizza retry' });
    const confirmButton = dialogButtons[dialogButtons.length - 1];
    expect(confirmButton).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '  Destinazione vuota verificata ' } });
    expect(confirmButton).not.toBeDisabled();
    fireEvent.click(confirmButton);

    await waitFor(() => expect(resolveUncertainCaricaRunMock).toHaveBeenCalledWith(
      'run-uncertain-1',
      'allow_retry',
      'Destinazione vuota verificata',
    ));
    expect(promptSpy).not.toHaveBeenCalled();
  });

  it('hides the resolution actions from who cannot resolve', async () => {
    getTaskRuntimeMock.mockResolvedValue(UNCERTAIN);
    authState.user = { role: 'admin', user_id: 'a' };
    try {
      render(<AdminTaskRuntimePage />);
      expect(await screen.findByText('run-uncertain-1')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Autorizza retry' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Conferma completata' })).toBeNull();
    } finally {
      authState.user = { role: 'superadmin', user_id: 's' };
    }
  });
});
