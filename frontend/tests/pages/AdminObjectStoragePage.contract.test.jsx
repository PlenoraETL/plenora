import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AdminObjectStoragePage from '../../src/pages/AdminObjectStoragePage';

const {
  cleanupOrphanStorageMock,
  deleteAllStoragePreviewsMock,
  deleteStorageObjectMock,
  downloadStorageObjectMock,
  listObjectStorageMock,
  listObjectStorageProjectsMock,
} = vi.hoisted(() => ({
  cleanupOrphanStorageMock: vi.fn(),
  deleteAllStoragePreviewsMock: vi.fn(),
  deleteStorageObjectMock: vi.fn(),
  downloadStorageObjectMock: vi.fn(),
  listObjectStorageMock: vi.fn(),
  listObjectStorageProjectsMock: vi.fn(),
}));

vi.mock('../../src/api/adminObjectStorage', () => ({
  cleanupOrphanStorage: cleanupOrphanStorageMock,
  deleteAllStoragePreviews: deleteAllStoragePreviewsMock,
  deleteStorageObject: deleteStorageObjectMock,
  downloadStorageObject: downloadStorageObjectMock,
  listObjectStorage: listObjectStorageMock,
  listObjectStorageProjects: listObjectStorageProjectsMock,
}));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));
vi.mock('../../src/context/AuthContext', () => ({ useAuth: () => ({ user: { role: 'superadmin', user_id: 's' } }) }));

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
    cleanupOrphanStorageMock.mockResolvedValue({
      orphan_results: { rows_deleted: 1 },
      errors: [],
    });
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
    // S7/G3: l'azione distruttiva sta nel menu «⋯», non accanto ad «Aggiorna».
    await user.click(screen.getByRole('button', { name: "Altre azioni dell'object storage" }));
    await user.click(screen.getByRole('menuitem', { name: 'Elimina tutte le preview' }));

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

  it('offers and applies cleanup when deleted projects retain results', async () => {
    listObjectStorageProjectsMock.mockResolvedValue({
      projects: [{
        project_id: 'gone',
        project_name: 'Progetto eliminato',
        orphan: true,
        project_type: null,
        group_id: 'g1',
        results: [{
          id: 'r-old',
          key: 'tenants/g1/results/gone/r-old.bin',
          filename: 'old.xlsx',
          format: 'xlsx',
          size_bytes: 42,
          rows_count: 1,
          created_at: '2026-07-14T12:00:00Z',
        }],
      }],
      results_count: 1,
      size_bytes: 42,
    });
    const user = userEvent.setup();
    render(<AdminObjectStoragePage />);

    await screen.findByText('Rilevati dati orfani');
    await user.click(screen.getByRole('button', { name: 'Bonifica orfani' }));
    expect(cleanupOrphanStorageMock).not.toHaveBeenCalled();
    const buttons = screen.getAllByRole('button', { name: 'Bonifica orfani' });
    await user.click(buttons.at(-1));

    await waitFor(() => expect(cleanupOrphanStorageMock).toHaveBeenCalledOnce());
    // Il perimetro richiesto e' quello mostrato (audit R10.4).
    expect(cleanupOrphanStorageMock).toHaveBeenCalledWith(['gone']);
    expect(await screen.findByText(
      'Bonifica completata: 1 risultati e 0 esecuzioni intermedie orfani eliminati',
    )).toBeInTheDocument();
  });

  it('un orfano con sole esecuzioni intermedie compare e la bonifica ne porta l\'id', async () => {
    listObjectStorageProjectsMock.mockResolvedValue({
      projects: [{
        project_id: 'gone-runs', project_name: 'Progetto eliminato', orphan: true, project_type: null,
        group_id: 'g1', results: [], intermediate_runs: 3,
      }],
      results_count: 0,
      size_bytes: 0,
    });
    cleanupOrphanStorageMock.mockResolvedValue({
      orphan_results: { rows_deleted: 0 }, orphan_runs: { runs_deleted: 3 }, errors: [],
    });
    const user = userEvent.setup();
    render(<AdminObjectStoragePage />);

    await screen.findByText('Rilevati dati orfani');
    expect(screen.getByText('3 esecuzioni intermedie')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Bonifica orfani' }));
    expect(screen.getByText(/0 risultati e 3 esecuzioni intermedie/)).toBeInTheDocument();
    const buttons = screen.getAllByRole('button', { name: 'Bonifica orfani' });
    await user.click(buttons.at(-1));

    await waitFor(() => expect(cleanupOrphanStorageMock).toHaveBeenCalledWith(['gone-runs']));
    expect(await screen.findByText(
      'Bonifica completata: 0 risultati e 3 esecuzioni intermedie orfani eliminati',
    )).toBeInTheDocument();
  });

  it('a project merely named "Progetto eliminato" is not an orphan (the server decides by id)', async () => {
    listObjectStorageProjectsMock.mockResolvedValue({
      projects: [{
        project_id: 'alive', project_name: 'Progetto eliminato', project_type: 'estrai', group_id: 'g1',
        results: [{ id: 'r', key: 'tenants/g1/results/alive/r.bin', filename: 'r.xlsx', format: 'xlsx', size_bytes: 1, rows_count: 1, created_at: '2026-07-14T12:00:00Z' }],
      }],
      results_count: 1,
      size_bytes: 1,
    });
    render(<AdminObjectStoragePage />);
    await screen.findAllByText('Progetto eliminato');
    expect(screen.queryByText('Rilevati dati orfani')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Bonifica orfani' })).toBeNull();
  });
});
