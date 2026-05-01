import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import ArricchisciEditor from '../../src/pages/ArricchisciEditor';

const { navigateMock, handleSaveQuietMock, setNodesMock, setEdgesMock, updateNodeDataMock, deleteNodeMock, modalsMock, toastMock, reactFlowStateRef, deserializeNodesMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  handleSaveQuietMock: vi.fn().mockResolvedValue(undefined),
  setNodesMock: vi.fn(),
  setEdgesMock: vi.fn(),
  updateNodeDataMock: vi.fn(),
  deleteNodeMock: vi.fn(),
  modalsMock: {
    open: vi.fn(),
    close: vi.fn(),
    isOpen: vi.fn(() => false),
  },
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  reactFlowStateRef: { current: { nodes: [], edges: [] } },
  deserializeNodesMock: vi.fn((nodes) => nodes),
}));

let currentProjectId = 'proj-1';
let currentEditorData = {
  project: {
    metadata: { name: 'Arricchisci Orders' },
    canvas: { nodes: [], edges: [] },
  },
  connections: [],
  loading: false,
  error: null,
  availableSources: [],
  availableFiles: [],
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

vi.mock('../../src/api', () => ({
  executeEnrichmentAsync: vi.fn(),
}));

vi.mock('../../src/api/files', () => ({
  getFiles: vi.fn(),
}));

vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => globalThis.__frontendTestHelpers.createAuthContextValue(vi),
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
    getViewportCenter: vi.fn(() => ({ x: 100, y: 120 })),
    isReadOnly: false,
    readOnlyProps: {},
  }),
  useMultiModal: () => modalsMock,
  useEnrichmentValidation: () => ({
    validation: null,
    estimation: null,
    isValidating: false,
    isEstimating: false,
    formatTime: vi.fn(),
    validateAndEstimate: vi.fn(),
    reset: vi.fn(),
  }),
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
      updateNodeData: vi.fn(),
    }),
  },
}));

vi.mock('../../src/utils/nodeFactories', () => ({
  deserializeNodes: deserializeNodesMock,
  createEdgeFromData: vi.fn(),
}));

vi.mock('../../src/components/panels/EditorSidebar', () => ({
  default: () => <div data-testid="editor-sidebar" />,
}));
vi.mock('../../src/components/nodes/ProjectSourceNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/FileSourceNode', () => ({ default: () => null }));
vi.mock('../../src/components/panels/ApiEnrichNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/ResultNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/CaricanNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/CaricanArcGISNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/ImdfPublisherNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/StaticValuesNode', () => ({ default: () => null }));
vi.mock('../../src/components/common', () => ({
  LoadingOverlay: ({ message }) => <div>{message}</div>,
}));
vi.mock('../../src/components/common/ErrorBoundary', () => ({
  CanvasErrorBoundary: ({ children }) => <>{children}</>,
  ModalErrorBoundary: ({ children }) => <>{children}</>,
}));
vi.mock('../../src/utils/hexCanvas', () => ({
  useHexCanvas: () => {},
}));

describe('ArricchisciEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentProjectId = 'proj-1';
    currentEditorData = {
      project: {
        metadata: { name: 'Arricchisci Orders' },
        canvas: { nodes: [], edges: [] },
      },
      connections: [],
      loading: false,
      error: null,
      availableSources: [],
      availableFiles: [],
      reload: vi.fn(),
    };
    reactFlowStateRef.current = { nodes: [], edges: [] };
    deserializeNodesMock.mockImplementation((nodes) => nodes);
  });

  it('redirects to home when the route projectId is invalid', async () => {
    globalThis.__frontendConsolePolicy.allowError = true;
    currentProjectId = 'bad-id';

    render(<ArricchisciEditor />);

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith('/', { replace: true });
    });
  });

  it('renders the project title and wires the save button to handleSaveQuiet', async () => {
    render(<ArricchisciEditor />);

    expect(await screen.findByText('Arricchisci Orders')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /salva/i }));

    await waitFor(() => {
      expect(handleSaveQuietMock).toHaveBeenCalledTimes(1);
    });
  });

  it('hydrates the saved canvas through deserializeNodes and sanitizes undefined edge handles', async () => {
    const loadedNodes = [{ id: 'loaded-1', type: 'project_source', data: { project_name: 'Upstream' } }];
    deserializeNodesMock.mockReturnValueOnce(loadedNodes);
    currentEditorData = {
      ...currentEditorData,
      project: {
        metadata: { name: 'Arricchisci Orders' },
        canvas: {
          nodes: [{ id: 'raw-1', type: 'project_source' }],
          edges: [
            { id: 'e-1', source: 'raw-1', target: 'result-1', sourceHandle: 'undefined', targetHandle: 'undefined' },
          ],
        },
      },
    };

    render(<ArricchisciEditor />);

    await waitFor(() => {
      expect(deserializeNodesMock).toHaveBeenCalledWith(
        currentEditorData.project.canvas.nodes,
        { flags: { isArricchisci: true } },
      );
      expect(setNodesMock).toHaveBeenCalledWith(loadedNodes);
      expect(setEdgesMock).toHaveBeenCalledWith([
        {
          id: 'e-1',
          source: 'raw-1',
          target: 'result-1',
          sourceHandle: 'output',
          targetHandle: 'input',
        },
      ]);
    });
  });

  it('adds a project source node from the canvas drop payload and then saves through the current editor state', async () => {
    const { container } = render(<ArricchisciEditor />);
    expect(await screen.findByText('Arricchisci Orders')).toBeInTheDocument();

    const canvas = container.querySelector('.arricchisci-canvas');
    const dataTransfer = {
      getData: vi.fn((key) => {
        if (key === 'application/reactflow') return 'project_source';
        if (key === 'nodeData') return JSON.stringify({ id: 'proj-upstream-1' });
        return '';
      }),
      dropEffect: 'move',
    };

    fireEvent.drop(canvas, {
      dataTransfer,
      clientX: 320,
      clientY: 180,
    });

    expect(setNodesMock).toHaveBeenCalled();
    const addNodeUpdater = setNodesMock.mock.calls.at(-1)[0];
    expect(typeof addNodeUpdater).toBe('function');

    const nextNodes = addNodeUpdater([]);
    expect(nextNodes).toHaveLength(1);
    expect(nextNodes[0]).toMatchObject({
      type: 'project_source',
      data: {
        source_project_id: 'proj-upstream-1',
        columns: [],
      },
    });

    await userEvent.click(screen.getByRole('button', { name: /salva/i }));

    await waitFor(() => {
      expect(handleSaveQuietMock).toHaveBeenCalledTimes(1);
    });
  });

  // ── Loading / error / project-not-found branches ────────────────────

  it('renders the loading overlay while useEditorData reports loading', () => {
    currentEditorData = { ...currentEditorData, loading: true };

    render(<ArricchisciEditor />);

    expect(screen.getByText('Caricamento progetto...')).toBeInTheDocument();
    expect(screen.queryByText('Arricchisci Orders')).not.toBeInTheDocument();
  });

  it('renders the load-error UI with Riprova and Torna alla lista buttons', () => {
    currentEditorData = {
      ...currentEditorData,
      error: 'arricchisci load failed',
    };

    render(<ArricchisciEditor />);

    expect(
      screen.getByText(/Errore caricamento:.*arricchisci load failed/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Riprova/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Torna alla lista/i }),
    ).toBeInTheDocument();
  });

  it('renders the "Progetto non trovato" empty branch when project is null', () => {
    currentEditorData = { ...currentEditorData, project: null };

    render(<ArricchisciEditor />);

    expect(screen.getByText('Progetto non trovato')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Torna alla lista/i }),
    ).toBeInTheDocument();
  });
});
