import React from 'react';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AdminTaskRuntimePage from '../../src/pages/AdminTaskRuntimePage';

const { getTaskRuntimeMock } = vi.hoisted(() => ({
  getTaskRuntimeMock: vi.fn(),
}));

vi.mock('../../src/api/adminTaskRuntime', () => ({
  getTaskRuntime: getTaskRuntimeMock,
}));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));

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
    });
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
});
