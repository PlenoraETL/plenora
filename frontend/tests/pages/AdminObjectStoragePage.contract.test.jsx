import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AdminObjectStoragePage from '../../src/pages/AdminObjectStoragePage';

const {
  deleteAllStoragePreviewsMock,
  deleteStorageObjectMock,
  downloadStorageObjectMock,
  listObjectStorageMock,
  listObjectStorageProjectsMock,
} = vi.hoisted(() => ({
  deleteAllStoragePreviewsMock: vi.fn(),
  deleteStorageObjectMock: vi.fn(),
  downloadStorageObjectMock: vi.fn(),
  listObjectStorageMock: vi.fn(),
  listObjectStorageProjectsMock: vi.fn(),
}));

vi.mock('../../src/api/adminObjectStorage', () => ({
  deleteAllStoragePreviews: deleteAllStoragePreviewsMock,
  deleteStorageObject: deleteStorageObjectMock,
  downloadStorageObject: downloadStorageObjectMock,
  listObjectStorage: listObjectStorageMock,
  listObjectStorageProjects: listObjectStorageProjectsMock,
}));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));

const objectPage = {
  bucket: 'runtime-test',
  next_cursor: null,
  objects: [{
    key: 'tenants/g1/results/p1/r1.bin',
    tenant_group_id: 'g1',
    domain: 'results',
    resource_id: 'p1',
    size_bytes: 42,
    last_modified: '2026-07-14T12:00:00Z',
  }],
};

describe('AdminObjectStoragePage delete contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listObjectStorageMock.mockResolvedValue(objectPage);
    listObjectStorageProjectsMock.mockResolvedValue({
      projects: [{
        project_id: 'p1',
        project_name: 'Project 1',
        project_type: 'estrai',
        group_id: 'g1',
        results: [{
          id: 'r1',
          key: 'tenants/g1/results/p1/r1.bin',
          filename: 'result.xlsx',
          format: 'xlsx',
          size_bytes: 42,
          rows_count: 1,
          created_at: '2026-07-14T12:00:00Z',
        }],
      }],
      results_count: 1,
      size_bytes: 42,
    });
    deleteStorageObjectMock.mockResolvedValue({ deleted: true });
    deleteAllStoragePreviewsMock.mockResolvedValue({ deleted: true, deleted_count: 2 });
  });

  it('requires explicit confirmation, deletes, and refreshes the page', async () => {
    const user = userEvent.setup();
    render(<AdminObjectStoragePage />);

    await screen.findByText('Project 1');
    await user.selectOptions(screen.getByLabelText('Tipo'), '');
    await screen.findByText('tenants/g1/results/p1/r1.bin');
    await user.click(screen.getByRole('button', { name: 'Elimina' }));

    expect(deleteStorageObjectMock).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole('button', { name: 'Elimina definitivamente' }),
    );

    await waitFor(() => expect(deleteStorageObjectMock).toHaveBeenCalledWith(
      'tenants/g1/results/p1/r1.bin',
    ));
    await waitFor(() => expect(listObjectStorageMock.mock.calls.length).toBeGreaterThanOrEqual(3));
  });

  it('purges only after confirmation and reports the deleted preview count', async () => {
    const user = userEvent.setup();
    render(<AdminObjectStoragePage />);

    await screen.findByText('Project 1');
    await user.click(screen.getByRole('button', { name: 'Elimina tutte le preview' }));

    expect(deleteAllStoragePreviewsMock).not.toHaveBeenCalled();
    const confirmationButtons = screen.getAllByRole(
      'button',
      { name: 'Elimina tutte le preview' },
    );
    await user.click(confirmationButtons.at(-1));

    await waitFor(() => expect(deleteAllStoragePreviewsMock).toHaveBeenCalledWith({
      scope: 'canonical',
      group_id: undefined,
    }));
    expect(await screen.findByText('Preview eliminate: 2')).toBeInTheDocument();
  });
});
