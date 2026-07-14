import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AdminObjectStoragePage from '../../src/pages/AdminObjectStoragePage';

const { deleteStorageObjectMock, downloadStorageObjectMock, listObjectStorageMock } = vi.hoisted(() => ({
  deleteStorageObjectMock: vi.fn(),
  downloadStorageObjectMock: vi.fn(),
  listObjectStorageMock: vi.fn(),
}));

vi.mock('../../src/api/adminObjectStorage', () => ({
  deleteStorageObject: deleteStorageObjectMock,
  downloadStorageObject: downloadStorageObjectMock,
  listObjectStorage: listObjectStorageMock,
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
    deleteStorageObjectMock.mockResolvedValue({ deleted: true });
  });

  it('requires explicit confirmation, deletes, and refreshes the page', async () => {
    const user = userEvent.setup();
    render(<AdminObjectStoragePage />);

    await screen.findByText('tenants/g1/results/p1/r1.bin');
    await user.click(screen.getByRole('button', { name: 'Elimina' }));

    expect(deleteStorageObjectMock).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole('button', { name: 'Elimina definitivamente' }),
    );

    await waitFor(() => expect(deleteStorageObjectMock).toHaveBeenCalledWith(
      'tenants/g1/results/p1/r1.bin',
    ));
    await waitFor(() => expect(listObjectStorageMock).toHaveBeenCalledTimes(2));
  });
});
