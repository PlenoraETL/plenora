import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import EstraiEditor from '../../src/pages/EstraiEditor';

const {
  navigateMock,
  handleSaveQuietMock,
  setNodesMock,
  setEdgesMock,
  deserializeNodesMock,
  createEdgeFromDataMock,
  updateNodeDataMock,
  deleteNodeMock,
  toastMock,
  reactFlowStateRef,
  validationStateRef,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  handleSaveQuietMock: vi.fn().mockResolvedValue(undefined),
  setNodesMock: vi.fn(),
  setEdgesMock: vi.fn(),
  deserializeNodesMock: vi.fn((nodes) => nodes),
  createEdgeFromDataMock: vi.fn((edge) => edge),
  updateNodeDataMock: vi.fn(),
  deleteNodeMock: vi.fn(),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  reactFlowStateRef: { current: { nodes: [], edges: [] } },
}));

let currentProjectId = 'proj-1';
let currentEditorData = {
  project: {
    metadata: { name: 'Estrai Orders' },
    canvas: { nodes: [], edges: [] },
  },
  connections: [],
  loading: false,
  error: null,
  catalog: {},
  views: {},
  projectConnection: { id: 'conn-1', name: 'Main DB', type: 'postgres' },
  sourceType: 'database',
  reload: vi.fn(),
};

vi.mock('reactflow', () => globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef: reactFlowStateRef }));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useParams: () => ({ projectId: currentProjectId }),
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../src/utils/validateRouteParam', () => ({
  isValidProjectId: (value) => value === 'proj-1',
}));

vi.mock('../../src/api/features', () => ({
  getLayerSchema: vi.fn(),
}));

vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => globalThis.__frontendTestHelpers.createAuthContextValue(vi, {
    user: { role: 'admin', username: 'tester', email: 'test@example.com' },
  }),
}));

vi.mock('../../src/hooks/useEditorData', () => ({
  useEditorData: () => currentEditorData,
}));

vi.mock('../../src/hooks', () => ({
  usePreview: () => ({
    showModal: false,
    closeModal: vi.fn(),
    handlePreview: vi.fn(),
    data: null,
    loading: false,
    error: null,
  }),
  useExecution: () => ({
    showModal: false,
    closeModal: vi.fn(),
    isExecuting: false,
    result: null,
    progress: 0,
    progressMessage: '',
    taskStatus: null,
    cancelTask: vi.fn(),
    caricaTasks: [],
    caricaPolling: false,
    handleExecute: vi.fn(),
    downloadTaskResult: vi.fn(),
  }),
  useValidationHighlights: () => {},
  useEdgeHandlers: () => ({
    onConnect: vi.fn(),
    onEdgeClick: vi.fn(),
    showJoinModal: false,
    editingEdge: null,
    pendingEdge: null,
    sourceNode: null,
    targetNode: null,
    handleSaveJoin: vi.fn(),
    handleCancelJoin: vi.fn(),
    handleDeleteEdge: vi.fn(),
    isEditing: false,
  }),
  useCanvasCore: () => ({
    nodes: [],
    edges: [],
    setNodes: setNodesMock,
    setEdges: setEdgesMock,
    onNodesChange: vi.fn(),
    onEdgesChange: vi.fn(),
    nodesRef: { current: [] },
    edgesRef: { current: [] },
    updateNodeData: updateNodeDataMock,
    deleteNode: deleteNodeMock,
    handleSaveQuiet: handleSaveQuietMock,
    dataNodes: [],
    getViewportCenter: vi.fn(() => ({ x: 90, y: 100 })),
    isReadOnly: false,
    readOnlyProps: {},
  }),
  useCanvasSave: (saveFn) => ({
    saving: false,
    handleSaveCanvas: async () => {
      try {
        await saveFn();
        toastMock.success('Progetto salvato!');
      } catch {
        toastMock.error('Errore durante il salvataggio');
      }
    },
  }),
  useUnsavedChangesGuard: () => ({ confirmNavigation: () => true }),
  useCaricarica: () => ({
    showModal: false,
    closeModal: vi.fn(),
    isExecuting: false,
    result: null,
    progress: 0,
    progressMessage: '',
    taskStatus: null,
    caricaricaInfo: {},
    cancelTask: vi.fn(),
    handleExecute: vi.fn(),
  }),
}));

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/store/useCanvasStore', () => ({
  default: {
    getState: () => ({
      markClean: vi.fn(),
      setEditorActions: vi.fn(),
      setConnections: vi.fn(),
    }),
  },
}));

vi.mock('../../src/utils/nodeFactories', () => ({
  deserializeNodes: deserializeNodesMock,
  createEdgeFromData: createEdgeFromDataMock,
}));

vi.mock('../../src/utils/columnUtils', () => ({
  calculateOutputColumnsRecursive: vi.fn(() => []),
}));

vi.mock('../../src/components/nodes/TableNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/FeatureLayerNode', () => ({ default: () => null }));
vi.mock('../../src/components/panels/FeatureServicePanel', () => ({ default: () => null }));
vi.mock('../../src/components/panels/QueryPreview', () => ({ default: () => <div data-testid="query-preview" /> }));
vi.mock('../../src/components/nodes/ResultNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/CaricanNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/CaricanArcGISNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/StaticValuesNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/AnnotationNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/DataSourceNode', () => ({ default: () => null }));
vi.mock('../../src/components/common/LoadingOverlay', () => ({
  LoadingOverlay: ({ message }) => <div>{message}</div>,
}));
vi.mock('../../src/components/common/ErrorBoundary', () => ({
  CanvasErrorBoundary: ({ children }) => <>{children}</>,
  ModalErrorBoundary: ({ children }) => <>{children}</>,
}));
vi.mock('../../src/utils/hexCanvas', () => ({
  useHexCanvas: () => {},
}));

describe('EstraiEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentProjectId = 'proj-1';
    reactFlowStateRef.current = { nodes: [], edges: [] };
    deserializeNodesMock.mockImplementation((nodes) => nodes);
    createEdgeFromDataMock.mockImplementation((edge) => edge);
    currentEditorData = {
      project: {
        metadata: { name: 'Estrai Orders' },
        canvas: { nodes: [], edges: [] },
      },
      connections: [],
      loading: false,
      error: null,
      catalog: {},
      views: {},
      projectConnection: { id: 'conn-1', name: 'Main DB', type: 'postgres' },
      sourceType: 'database',
      reload: vi.fn(),
    };
  });

  it('redirects to home when the route projectId is invalid', async () => {
    globalThis.__frontendConsolePolicy.allowError = true;
    currentProjectId = 'bad-id';

    render(<EstraiEditor />);

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith('/', { replace: true });
    });
  });

  it('renders the project title and saves through handleSaveQuiet', async () => {
    render(<EstraiEditor />);

    expect(await screen.findByText('Estrai Orders')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /salva/i }));

    await waitFor(() => {
      expect(handleSaveQuietMock).toHaveBeenCalledTimes(1);
      expect(toastMock.success).toHaveBeenCalledWith('Progetto salvato!');
    });
  });

  it('hydrates the canvas through deserializeNodes and createEdgeFromData', async () => {
    const storedNodes = [
      { id: 'table-1', type: 'tableNode', data: { label: 'orders' } },
      { id: 'result-1', type: 'resultNode', data: { fileName: 'orders' } },
    ];
    const loadedNodes = storedNodes.map((node) => ({
      ...node,
      data: { ...node.data, hydrated: true },
    }));
    const storedEdges = [
      { id: 'edge-1', source: 'table-1', target: 'result-1', sourceHandle: 'orders' },
    ];
    const builtEdge = { ...storedEdges[0], type: 'smoothstep', hydrated: true };

    deserializeNodesMock.mockReturnValueOnce(loadedNodes);
    createEdgeFromDataMock.mockReturnValueOnce(builtEdge);
    currentEditorData = {
      ...currentEditorData,
      project: {
        metadata: { name: 'Estrai Orders' },
        canvas: { nodes: storedNodes, edges: storedEdges },
      },
      projectConnection: { id: 'conn-99', name: 'Warehouse', type: 'mysql' },
    };

    render(<EstraiEditor />);

    expect(await screen.findByText('Estrai Orders')).toBeInTheDocument();

    await waitFor(() => {
      expect(deserializeNodesMock).toHaveBeenCalledWith(storedNodes, {
        connection: currentEditorData.projectConnection,
      });
      expect(createEdgeFromDataMock).toHaveBeenCalledWith(storedEdges[0], loadedNodes);
      expect(setNodesMock).toHaveBeenCalledWith(loadedNodes);
      expect(setEdgesMock).toHaveBeenCalledWith([builtEdge]);
    });
  });


  it('renders the loading overlay while useEditorData reports loading', () => {
    currentEditorData = { ...currentEditorData, loading: true };

    render(<EstraiEditor />);

    expect(screen.getByText('Caricamento progetto...')).toBeInTheDocument();
    // Title NOT rendered (early return).
    expect(screen.queryByText('Estrai Orders')).not.toBeInTheDocument();
  });

  it('renders the load-error UI with Riprova and Torna alla lista buttons', () => {
    currentEditorData = { ...currentEditorData, error: 'database unreachable' };

    render(<EstraiEditor />);

    expect(
      screen.getByText(/Errore caricamento:.*database unreachable/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Riprova/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Torna alla lista/i }),
    ).toBeInTheDocument();
  });

  it('toasts an error when handleSaveQuiet rejects on Save click', async () => {
    globalThis.__frontendConsolePolicy.allowError = true;
    handleSaveQuietMock.mockRejectedValueOnce(new Error('persistence boom'));

    render(<EstraiEditor />);
    await screen.findByText('Estrai Orders');
    await userEvent.click(screen.getByRole('button', { name: /salva/i }));

    await waitFor(() => {
      expect(handleSaveQuietMock).toHaveBeenCalledTimes(1);
      expect(toastMock.error).toHaveBeenCalledWith('Errore durante il salvataggio');
    });
    expect(toastMock.success).not.toHaveBeenCalled();
  });

  it('saves drafts regardless of validation (validazione on-demand, non blocca il save)', async () => {
    render(<EstraiEditor />);
    await screen.findByText('Estrai Orders');
    await userEvent.click(screen.getByRole('button', { name: /salva/i }));

    await waitFor(() => {
      expect(handleSaveQuietMock).toHaveBeenCalledTimes(1);
      expect(toastMock.success).toHaveBeenCalledWith('Progetto salvato!');
    });
    expect(toastMock.error).not.toHaveBeenCalled();
  });
});
