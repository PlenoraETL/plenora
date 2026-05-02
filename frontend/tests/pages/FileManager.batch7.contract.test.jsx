import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import FileManager from '../../src/pages/FileManager';

/**
 * Batch 7 di FE-TEST-001 su FileManager — gap ad alto valore non
 * coperti dal contract test esistente (FileManager.contract.test.jsx,
 * focalizzato su preview happy path).
 *
 * Aree:
 *   1. delete success: confirm + deleteFile + reload
 *   2. search filter restringe la lista per nome/original_filename
 *   3. filter tab "csv" mostra solo i file CSV
 *   4. preview 404 fallback: toast.error + reload (RB.5 race condition)
 *   5. role gating: viewer NON vede "↑ Carica File" ne' la drop zone
 *
 * Pattern di mocking allineato ai batch precedenti; useConfirmModal
 * mockato con Promise auto-resolve (test esistente lo lasciava
 * incompleto, ma il preview path non lo invocava).
 */

const {
  navigateMock,
  toastMock,
  getFilesMock,
  deleteFileMock,
  getFilePreviewMock,
  authState,
  confirmState,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
  getFilesMock: vi.fn(),
  deleteFileMock: vi.fn(),
  getFilePreviewMock: vi.fn(),
  authState: {
    user: { role: 'admin', username: 'tester', group_id: 'grp-1' },
  },
  confirmState: { result: true },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
vi.mock('../../src/hooks/useConfirmModal', () => ({
  useConfirmModal: () => ({
    confirm: () => Promise.resolve(confirmState.result),
    isOpen: false,
    config: {},
    handleConfirm: vi.fn(),
    handleCancel: vi.fn(),
  }),
}));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({ user: authState.user, logout: vi.fn() }),
}));
vi.mock('../../src/api/files', () => ({
  getFiles: getFilesMock,
  uploadFile: vi.fn(),
  uploadMultiLayer: vi.fn(),
  deleteFile: deleteFileMock,
  getFileLayers: vi.fn(),
  getFilePreview: getFilePreviewMock,
  previewFileTemp: vi.fn(),
}));
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));
vi.mock('../../src/components/shared/AllowedGroupsSelector', () => ({
  default: () => null,
}));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));
vi.mock('../../src/components/common/ConfirmDialog', () => ({
  default: () => null,
}));

const sampleCsv = {
  id: 'file-1',
  name: 'Cities',
  original_filename: 'cities.csv',
  description: 'POI list',
  format: 'csv',
  row_count: 100,
  file_size: 4096,
  columns: [{ name: 'id' }, { name: 'name' }],
  can_edit: true,
};
const sampleXlsx = {
  id: 'file-2',
  name: 'Orders',
  original_filename: 'orders.xlsx',
  format: 'xlsx',
  row_count: 50,
  file_size: 2048,
  columns: [{ name: 'order_id' }],
  can_edit: true,
};

describe('FileManager — batch 7', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'admin', username: 'tester', group_id: 'grp-1' };
    confirmState.result = true;
    getFilesMock.mockResolvedValue({ files: [] });
  });

  // ── 1. Delete success ────────────────────────────────────────────────

  it('deletes a file after confirm and reloads the list', async () => {
    getFilesMock
      .mockResolvedValueOnce({ files: [sampleCsv] })
      .mockResolvedValueOnce({ files: [] });
    deleteFileMock.mockResolvedValueOnce({ ok: true });

    render(<FileManager />);
    await screen.findByText('Cities');

    // Trash icon su FileCard.
    await userEvent.click(screen.getByRole('button', { name: /Elimina file/i }));

    await waitFor(() =>
      expect(deleteFileMock).toHaveBeenCalledWith('file-1'),
    );
    // Reload: getFiles chiamato 2 volte (initial + post-delete).
    expect(getFilesMock).toHaveBeenCalledTimes(2);
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  // ── 2. Search filter ────────────────────────────────────────────────

  it('filters file cards by the search input (name or original_filename)', async () => {
    getFilesMock.mockResolvedValueOnce({ files: [sampleCsv, sampleXlsx] });

    render(<FileManager />);
    await screen.findByText('Cities');
    expect(screen.getByText('Orders')).toBeInTheDocument();

    const search = screen.getByPlaceholderText(/cerca file/i);
    await userEvent.type(search, 'order');

    expect(screen.getByText('Orders')).toBeInTheDocument();
    expect(screen.queryByText('Cities')).not.toBeInTheDocument();
  });

  // ── 3. Filter tab CSV ───────────────────────────────────────────────

  it('shows only CSV files when the CSV filter tab is active', async () => {
    getFilesMock.mockResolvedValueOnce({ files: [sampleCsv, sampleXlsx] });

    render(<FileManager />);
    await screen.findByText('Cities');
    expect(screen.getByText('Orders')).toBeInTheDocument();

    // Click sul tab "CSV" (FILTER_TABS).
    const csvTab = screen.getByRole('button', { name: /^CSV/i });
    await userEvent.click(csvTab);

    expect(screen.getByText('Cities')).toBeInTheDocument();
    expect(screen.queryByText('Orders')).not.toBeInTheDocument();
  });

  // ── 4. Preview 404 fallback ─────────────────────────────────────────

  it('refreshes the list and toasts when getFilePreview returns 404 (RB.5)', async () => {
    // Initial list has the file; reload after 404 returns empty.
    getFilesMock
      .mockResolvedValueOnce({ files: [sampleCsv] })
      .mockResolvedValueOnce({ files: [] });
    getFilePreviewMock.mockRejectedValueOnce({
      response: { status: 404 },
      message: 'Not found',
    });

    render(<FileManager />);
    await screen.findByText('Cities');

    await userEvent.click(screen.getByRole('button', { name: /preview/i }));

    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    const [msg] = toastMock.error.mock.calls[0];
    expect(msg).toMatch(/File non trovato/i);
    expect(msg).toMatch(/Aggiorno la lista/i);
    // Reload triggered after 404.
    expect(getFilesMock).toHaveBeenCalledTimes(2);
  });

  // ── 5. Role gating viewer ───────────────────────────────────────────

  it('hides upload button and drop zone for viewer role', async () => {
    authState.user = { role: 'viewer', username: 'v', group_id: 'grp-1' };
    getFilesMock.mockResolvedValueOnce({ files: [sampleCsv] });

    render(<FileManager />);
    await screen.findByText('Cities');

    // can.uploadFile = isUserOrAbove → viewer NON puo' uploadare.
    expect(
      screen.queryByRole('button', { name: /Carica File/i }),
    ).not.toBeInTheDocument();
    // Drop zone hint NON deve essere visibile.
    expect(
      screen.queryByText(/Trascina i file qui/i),
    ).not.toBeInTheDocument();
    // Sanity: il viewer puo' comunque vedere le card e fare preview.
    expect(screen.getByText('Cities')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /preview/i }),
    ).toBeInTheDocument();
  });
});
