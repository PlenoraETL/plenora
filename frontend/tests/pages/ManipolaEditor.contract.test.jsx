import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import ManipolaEditor from '../../src/pages/ManipolaEditor';

const {
  navigateMock,
  handleSaveQuietMock,
  loadGraphMock,
  addNodesMock,
  deserializeNodesMock,
  propagateColumnsMock,
  refreshSchemasMock,
  updateNodeDataMock,
  deleteNodeMock,
  toastMock,
  reactFlowStateRef,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  handleSaveQuietMock: vi.fn().mockResolvedValue(undefined),
  loadGraphMock: vi.fn(),
  addNodesMock: vi.fn(),
  deserializeNodesMock: vi.fn((nodes) => nodes),
  propagateColumnsMock: vi.fn(),
  refreshSchemasMock: vi.fn(),
  updateNodeDataMock: vi.fn(),
  deleteNodeMock: vi.fn(),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  reactFlowStateRef: { current: { nodes: [], edges: [] } },
}));

let currentProjectId = 'proj-1';
let currentEditorData = {
  project: {
    metadata: { name: 'Manipola Orders' },
    canvas: { nodes: [], edges: [] },
  },
  connections: [],
  loading: false,
  error: null,
  availableSources: [],
  availableFiles: [],
  reload: vi.fn(),
};

vi.mock('@xyflow/react', () => globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef: reactFlowStateRef }));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    // La route reale e' /editor/manipola/:projectId (App.jsx): il param
    // si chiama projectId, non id.
    useParams: () => ({ projectId: currentProjectId }),
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../src/utils/validateRouteParam', () => ({
  isValidProjectId: (value) => value === 'proj-1',
}));

vi.mock('../../src/api', () => ({
  createTask: vi.fn(),
  getDownloadUrl: vi.fn(),
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
  useSchemaInference: () => ({
    schemas: {},
    loading: false,
    refreshSchemas: refreshSchemasMock,
  }),
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
    loadGraph: loadGraphMock,
    addNodes: addNodesMock,
    addEdges: vi.fn(),
    removeEdges: vi.fn(),
    editNodes: vi.fn(),
    editEdges: vi.fn(),
    updateEdge: vi.fn(),
    patchNodeConfig: vi.fn(),
    applyDerivedNodes: vi.fn(),
    applyDerivedEdges: vi.fn(),
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
  useValidationHighlights: () => undefined,
}));

vi.mock('../../src/hooks/useColumnPropagation', () => ({
  default: () => ({
    propagateColumns: propagateColumnsMock,
  }),
}));

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/store/useCanvasStore', () => {
  const storeState = {
    markClean: vi.fn(),
    setEditorActions: vi.fn(),
    setConnections: vi.fn(),
    takeSnapshot: vi.fn(),
    validationHighlights: [],
    lastValidationSummary: null,
    past: [],
    future: [],
  };
  const hook = (selector) => selector ? selector(storeState) : storeState;
  hook.getState = () => storeState;
  return { default: hook };
});

vi.mock('../../src/utils/nodeFactories', () => ({
  deserializeNodes: deserializeNodesMock,
  createEdgeFromData: vi.fn(),
}));

vi.mock('../../src/components/panels/EditorSidebar', () => ({
  default: () => <div data-testid="editor-sidebar" />,
}));
vi.mock('../../src/components/nodes/ProjectSourceNode', () => ({ default: () => null }));
vi.mock('../../src/components/nodes/FileSourceNode', () => ({ default: () => null }));
vi.mock('../../src/components/transforms/TransformNode', () => ({ default: () => null }));
vi.mock('../../src/components/transforms/MultiJoinNode', () => ({ default: () => null }));
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

describe('ManipolaEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentProjectId = 'proj-1';
    reactFlowStateRef.current = { nodes: [], edges: [] };
    deserializeNodesMock.mockImplementation((nodes) => nodes);
    currentEditorData = {
      project: {
        metadata: { name: 'Manipola Orders' },
        canvas: { nodes: [], edges: [] },
      },
      connections: [],
      loading: false,
      error: null,
      availableSources: [],
      availableFiles: [],
      reload: vi.fn(),
    };
  });

  it('redirects to home when the route projectId is invalid', async () => {
    globalThis.__frontendConsolePolicy.allowError = true;
    currentProjectId = 'bad-id';

    render(<ManipolaEditor />);

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith('/', { replace: true });
    });
  });

  it('renders the project title and wires the save button to handleSaveQuiet', async () => {
    render(<ManipolaEditor />);

    expect(await screen.findByText('Manipola Orders')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /salva/i }));

    await waitFor(() => {
      expect(handleSaveQuietMock).toHaveBeenCalledTimes(1);
    });
  });

  it('hydrates manipola nodes and normalizes missing target handles from stored edges', async () => {
    const storedNodes = [
      { id: 'source-1', type: 'project_source', data: { source_project_id: 'proj-upstream' } },
      { id: 'transform-1', type: 'transform', data: { transform_op: 'filter' } },
    ];
    const loadedNodes = storedNodes.map((node) => ({
      ...node,
      data: { ...node.data, hydrated: true },
    }));
    const storedEdges = [
      { id: 'edge-transform-1-input-2', source: 'source-1', target: 'transform-1' },
    ];

    deserializeNodesMock.mockReturnValueOnce(loadedNodes);
    currentEditorData = {
      ...currentEditorData,
      project: {
        metadata: { name: 'Manipola Orders' },
        canvas: { nodes: storedNodes, edges: storedEdges },
      },
    };

    render(<ManipolaEditor />);

    expect(await screen.findByText('Manipola Orders')).toBeInTheDocument();

    await waitFor(() => {
      expect(deserializeNodesMock).toHaveBeenCalledWith(storedNodes, {
        flags: { isManipulate: true },
      });
      expect(loadGraphMock).toHaveBeenCalledWith(loadedNodes, [
        {
          ...storedEdges[0],
          targetHandle: 'input-2',
        },
      ]);
    });

    await waitFor(() => {
      expect(refreshSchemasMock).toHaveBeenCalled();
      expect(propagateColumnsMock).toHaveBeenCalledWith(loadedNodes, [
        {
          ...storedEdges[0],
          targetHandle: 'input-2',
        },
      ]);
    });
  });


  it('renders the loading overlay while useEditorData reports loading', () => {
    currentEditorData = { ...currentEditorData, loading: true };

    render(<ManipolaEditor />);

    expect(screen.getByText('Caricamento progetto...')).toBeInTheDocument();
    expect(screen.queryByText('Manipola Orders')).not.toBeInTheDocument();
  });

  it('renders the load-error UI with Riprova and Torna alla lista buttons', () => {
    currentEditorData = { ...currentEditorData, error: 'manipola load failed' };

    render(<ManipolaEditor />);

    expect(
      screen.getByText(/Errore caricamento:.*manipola load failed/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Riprova/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Torna alla lista/i }),
    ).toBeInTheDocument();
  });

  it('toasts "Errore durante il salvataggio" when handleSaveQuiet rejects', async () => {
    globalThis.__frontendConsolePolicy.allowError = true;
    handleSaveQuietMock.mockRejectedValueOnce(new Error('persistence boom'));

    render(<ManipolaEditor />);
    await screen.findByText('Manipola Orders');
    await userEvent.click(screen.getByRole('button', { name: /salva/i }));

    await waitFor(() => {
      expect(handleSaveQuietMock).toHaveBeenCalledTimes(1);
      expect(toastMock.error).toHaveBeenCalledWith('Errore durante il salvataggio');
    });
  });
});
