import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import FileManager from '../../src/pages/FileManager';


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
  updateFile: vi.fn(),
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

describe('FileManager contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'admin', username: 'tester', group_id: 'grp-1' };
    confirmState.result = true;
    getFilesMock.mockResolvedValue({ files: [] });
  });
  it('deletes a file after confirm and reloads the list', async () => {
    getFilesMock
      .mockResolvedValueOnce({ files: [sampleCsv] })
      .mockResolvedValueOnce({ files: [] });
    deleteFileMock.mockResolvedValueOnce({ ok: true });

    render(<FileManager />);
    await screen.findByText('Cities');
    await userEvent.click(screen.getByRole('button', { name: /Elimina file/i }));

    await waitFor(() =>
      expect(deleteFileMock).toHaveBeenCalledWith('file-1'),
    );
    expect(getFilesMock).toHaveBeenCalledTimes(2);
    expect(toastMock.error).not.toHaveBeenCalled();
  });
  it('filters file cards by the search input (name or original_filename)', async () => {
    getFilesMock.mockResolvedValueOnce({ files: [sampleCsv, sampleXlsx] });

    render(<FileManager />);
    await screen.findByText('Cities');
    expect(screen.getByText('Orders')).toBeInTheDocument();

    const search = screen.getByPlaceholderText(/cerca file/i);
    await userEvent.type(search, 'order');

    // P28: la ricerca si applica dopo la pausa unica (hooks/useSearch).
    await waitFor(() => expect(screen.queryByText('Cities')).not.toBeInTheDocument());
    expect(screen.getByText('Orders')).toBeInTheDocument();
  });
  it('shows only CSV files when the CSV filter tab is active', async () => {
    getFilesMock.mockResolvedValueOnce({ files: [sampleCsv, sampleXlsx] });

    render(<FileManager />);
    await screen.findByText('Cities');
    expect(screen.getByText('Orders')).toBeInTheDocument();
    const csvTab = screen.getByRole('button', { name: /^CSV/i });
    await userEvent.click(csvTab);

    expect(screen.getByText('Cities')).toBeInTheDocument();
    expect(screen.queryByText('Orders')).not.toBeInTheDocument();
  });
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
    expect(screen.getByText('Cities')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /preview/i }),
    ).toBeInTheDocument();
  });
});
