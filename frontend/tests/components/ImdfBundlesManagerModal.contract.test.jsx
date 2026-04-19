import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import ImdfBundlesManagerModal from '../../src/components/modals/ImdfBundlesManagerModal';

describe('ImdfBundlesManagerModal contract', () => {
  const onClose = vi.fn();
  const onSelect = vi.fn();
  const onDelete = vi.fn();

  const bundles = [
    {
      task_id: 'bundle-1',
      site_id: 'SITE-001',
      age_hours: 1.5,
      size_mb: 12,
      building_count: 3,
      level_count: 7,
      unit_count: 14,
      validation_summary: { passed: true, total_errors: 0 },
    },
    {
      task_id: 'bundle-2',
      site_id: 'SITE-002',
      age_hours: 5,
      size_mb: 20,
      building_count: 1,
      level_count: 2,
      unit_count: 4,
      validation_summary: { passed: false, total_errors: 2 },
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    onDelete.mockResolvedValue(undefined);
  });

  it('filters, selects and confirms deletion through the explicit two-click contract', async () => {
    render(
      <ImdfBundlesManagerModal
        isOpen
        onClose={onClose}
        bundles={bundles}
        selectedTaskId="bundle-1"
        onSelect={onSelect}
        onDelete={onDelete}
      />,
    );

    await userEvent.type(screen.getByPlaceholderText(/filtra per site_id/i), 'SITE-002');
    expect(screen.queryByText('SITE-001')).not.toBeInTheDocument();
    expect(screen.getByText('SITE-002')).toBeInTheDocument();

    await userEvent.clear(screen.getByPlaceholderText(/filtra per site_id/i));
    await userEvent.click(screen.getByRole('button', { name: 'Usa' }));
    expect(onSelect).toHaveBeenCalledWith('bundle-2');

    const deleteButtons = screen.getAllByRole('button', { name: '🗑' });
    await userEvent.click(deleteButtons[1]);
    expect(screen.getByRole('button', { name: /conferma/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /conferma/i }));

    await waitFor(() => {
      expect(onDelete).toHaveBeenCalledWith('bundle-2');
    });
  });
});
