import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import FileManager from '../../src/pages/FileManager';

const { navigateMock, toastMock, getFilesMock, getFilePreviewMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
  getFilesMock: vi.fn(),
  getFilePreviewMock: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/hooks/useConfirmModal', () => ({
  useConfirmModal: () => ({
    ask: vi.fn(),
  }),
}));

vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({
    user: { role: 'superadmin', username: 'tester', email: 'test@example.com' },
    logout: vi.fn(),
  }),
}));

vi.mock('../../src/api/files', () => ({
  getFiles: getFilesMock,
  updateFile: vi.fn(),
  uploadFile: vi.fn(),
  uploadMultiLayer: vi.fn(),
  deleteFile: vi.fn(),
  getFileLayers: vi.fn(),
  getFilePreview: getFilePreviewMock,
  previewFileTemp: vi.fn(),
}));

vi.mock('../../src/utils/hexCanvas', () => ({
  useHexCanvas: () => {},
}));

vi.mock('../../src/components/shared/AllowedGroupsSelector', () => ({
  default: () => <div data-testid="allowed-groups-selector" />,
}));

vi.mock('../../src/components/AppSidebar', () => ({
  default: () => <div data-testid="app-sidebar" />,
}));

vi.mock('../../src/components/common/ConfirmDialog', () => ({
  default: () => null,
}));

// Tabella reale dei permessi, con le sole capability del test forzate.
vi.mock('../../src/utils/permissions', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    can: {
      ...actual.can,
      uploadFile: () => true,
    },
    role: {
      ...actual.role,
      isSuperadmin: () => true,
    },
  };
});

describe('FileManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getFilesMock.mockResolvedValue({
      files: [
        {
          id: 'file-1',
          name: 'Cities',
          description: 'Preview me',
          format: 'csv',
          row_count: 2,
          columns: [{ name: 'id' }, { name: 'name' }],
          original_filename: 'cities.csv',
          can_edit: true,
        },
      ],
    });
    getFilePreviewMock.mockResolvedValue({
      columns: [{ name: 'id' }, { name: 'name' }],
      rows: [{ id: 1, name: 'Rome' }],
      total_rows: 1,
    });
  });

  it('loads file cards and opens the preview flow with the backend payload', async () => {
    render(<FileManager />);

    expect(await screen.findByText('Cities')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /anteprima/i }));

    await waitFor(() => {
      expect(getFilePreviewMock).toHaveBeenCalledWith('file-1', 50);
    });
    expect(await screen.findByText(/anteprima — cities/i)).toBeInTheDocument();
    expect(screen.getByText('Rome')).toBeInTheDocument();
  });

  it('shows the real 100 MB upload limit', async () => {
    render(<FileManager />);
    await screen.findByText('Cities');

    await userEvent.click(screen.getByRole('button', { name: /Carica File/i }));

    expect(await screen.findByText(/Max 100 MB per file/i)).toBeInTheDocument();
  });
});
