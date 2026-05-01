import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import ResultNode from '../../src/components/nodes/ResultNode';
import { ConfirmProvider } from '../../src/context/ConfirmContext';

const {
  onPreviewMock,
  onExecuteMock,
  updateNodeDataMock,
  deleteNodeMock,
  setLastTaskResultMock,
  listImdfBundlesMock,
  getImdfBundleMetadataMock,
  deleteImdfBundleMock,
  useCanvasStoreMock,
  canvasStateRef,
} = vi.hoisted(() => ({
  onPreviewMock: vi.fn(),
  onExecuteMock: vi.fn(),
  updateNodeDataMock: vi.fn(),
  deleteNodeMock: vi.fn(),
  setLastTaskResultMock: vi.fn(),
  listImdfBundlesMock: vi.fn(),
  getImdfBundleMetadataMock: vi.fn(),
  deleteImdfBundleMock: vi.fn(),
  useCanvasStoreMock: vi.fn(),
  canvasStateRef: { current: { nodes: [], edges: [] } },
}));

let canvasState;

vi.mock('reactflow', () => globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef, showRoot: false }));

vi.mock('../../src/store/useCanvasStore', () => ({
  default: useCanvasStoreMock,
}));

// useToast: useImdfBundles ora ne consuma uno per il path errore
// handleDeleteBundle (alert -> toast). Stub no-op: questo test non
// esercita il path errore.
vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => ({
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

vi.mock('../../src/utils/validateManipolaCanvas', () => ({
  validateManipolaCanvas: vi.fn(() => ({ isValid: true, issues: [] })),
}));

vi.mock('../../src/utils/validateEstraiCanvas', () => ({
  validateEstraiCanvas: vi.fn(() => ({ isValid: true, issues: [] })),
}));

vi.mock('../../src/utils/validateArricchisciCanvas', () => ({
  validateArricchisciCanvas: vi.fn(() => ({ isValid: true, issues: [] })),
}));

vi.mock('../../src/utils/columnUtils', () => ({
  getColumnTypeIcon: vi.fn(() => 'A'),
  getColumnTypeLabel: vi.fn(() => 'text'),
  getColumnSRID: vi.fn(() => null),
  isGeometryType: vi.fn(() => false),
}));

vi.mock('../../src/api/imdf', () => ({
  getImdfBundleMetadata: getImdfBundleMetadataMock,
  listImdfBundles: listImdfBundlesMock,
  deleteImdfBundle: deleteImdfBundleMock,
}));

vi.mock('../../src/api/apiConnections', () => ({
  listApiConnections: vi.fn().mockResolvedValue({ api_connections: [] }),
}));

vi.mock('../../src/components/modals/ValidationModal', () => ({
  default: ({ isOpen }) => (isOpen ? <div>Validation Modal</div> : null),
}));

vi.mock('../../src/components/modals/RunHistoryModal', () => ({
  default: ({ isOpen }) => (isOpen ? <div>Run History Modal</div> : null),
}));

vi.mock('../../src/components/modals/ImdfPreviewModal', () => ({
  default: ({ isOpen }) => (isOpen ? <div>IMDF Preview Modal</div> : null),
}));

vi.mock('../../src/components/modals/ImdfManifestModal', () => ({
  default: ({ isOpen, manifest }) =>
    isOpen ? <div>IMDF Manifest Modal {manifest?.site_name || 'loaded'}</div> : null,
}));

vi.mock('../../src/components/modals/ImdfValidationModal', () => ({
  default: ({ isOpen }) => (isOpen ? <div>IMDF Validation Modal</div> : null),
}));

vi.mock('../../src/components/modals/ImdfBundlesManagerModal', () => ({
  default: ({ isOpen, onDelete }) =>
    isOpen ? (
      <div>
        <span>IMDF Bundles Manager Modal</span>
        <button type="button" onClick={() => onDelete('bundle-2')}>Delete Bundle</button>
      </div>
    ) : null,
}));

vi.mock('../../src/components/imdf/BundleSlotCard', () => ({
  default: ({ bundleTaskId, onOpenManager }) => (
    <div>
      <span>Bundle Slot {bundleTaskId}</span>
      <button type="button" onClick={onOpenManager}>Open Manager</button>
    </div>
  ),
}));

describe('ResultNode contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    canvasState = {
      updateNodeData: updateNodeDataMock,
      deleteNode: deleteNodeMock,
      lastTaskResult: null,
      setLastTaskResult: setLastTaskResultMock,
      projectId: 'proj-1',
      editorActionsRef: {
        current: {
          onPreview: onPreviewMock,
          onExecute: onExecuteMock,
        },
      },
      getState: vi.fn(),
      nodes: [
        {
          id: 'src-1',
          type: 'api_enrich',
          data: { output_kind: 'imdf', columns: [] },
        },
        {
          id: 'result-1',
          type: 'result',
          data: {},
        },
      ],
      edges: [{ id: 'e1', source: 'src-1', target: 'result-1' }],
      transformsRegistry: null,
    };
    canvasStateRef.current = canvasState;
    useCanvasStoreMock.mockImplementation((selector) => selector(canvasState));
    useCanvasStoreMock.getState = () => canvasState;

    listImdfBundlesMock.mockResolvedValue([
      {
        task_id: 'bundle-2',
        site_id: 'SITE-002',
        validation_summary: { passed: true, total_errors: 0 },
      },
    ]);
    getImdfBundleMetadataMock.mockResolvedValue({
      site_id: 'SITE-002',
      manifest: { site_name: 'Demo Site' },
      validation: { passed: true },
    });
    deleteImdfBundleMock.mockResolvedValue(undefined);
  });

  it('hydrates the latest IMDF bundle, opens the manager and executes through the shared editor contract', async () => {
    render(
      // PR-B: ResultNode ora richiede ConfirmProvider nell'albero React
      // (useConfirm hook). Wrap minimal del test esistente.
      <ConfirmProvider>
        <ResultNode
          id="result-1"
          data={{
            isArricchisci: true,
            fileName: 'imdf-output',
            format: 'csv',
            columns: [],
            availableColumns: [],
            project_id: 'proj-1',
          }}
        />
      </ConfirmProvider>,
    );

    await waitFor(() => {
      expect(listImdfBundlesMock).toHaveBeenCalledWith({ project_id: 'proj-1' });
      expect(getImdfBundleMetadataMock).toHaveBeenCalledWith('bundle-2');
    });

    expect(setLastTaskResultMock).toHaveBeenCalledWith(
      expect.objectContaining({
        imdf: expect.objectContaining({
          bundle_task_id: 'bundle-2',
          site_id: 'SITE-002',
        }),
      }),
    );

    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /open manager/i }));
    expect(await screen.findByText(/imdf bundles manager modal/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /delete bundle/i }));
    expect(deleteImdfBundleMock).toHaveBeenCalledWith('bundle-2');

    await user.click(screen.getByRole('button', { name: /esegui arricchimento/i }));
    expect(onExecuteMock).toHaveBeenCalledWith('result-1', { force_recalculate: false });
  });
});
