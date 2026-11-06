import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import ArricchisciEditor from '../../src/pages/ArricchisciEditor';

const { navigateMock, handleSaveQuietMock, loadGraphMock, addNodesMock, updateNodeDataMock, deleteNodeMock, modalsMock, toastMock, reactFlowStateRef, deserializeNodesMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  handleSaveQuietMock: vi.fn().mockResolvedValue(undefined),
  loadGraphMock: vi.fn(),
  addNodesMock: vi.fn(),
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

vi.mock('../../src/hooks/useColumns', () => ({
  // Fonte unica delle colonne (Blocco A, regola 4): la sottoscrizione la
  // monta il guscio; qui la pagina non deve calcolare ne' propagare nulla.
  useColumnSync: () => undefined,
  useColumnsOf: () => ({ columns: [], state: {}, status: 'idle', error: null, known: false, inputs: [] }),
  getColumnsOf: () => ({ columns: [], state: {}, status: 'idle', error: null, known: false, inputs: [] }),
  columnSnapshot: () => ({ schemas: {}, states: {}, nodes: [], edges: [], mode: 'manipola' }),
  refreshColumns: () => undefined,
}));

vi.mock('@xyflow/react', () => globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef: reactFlowStateRef }));

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
  useMultiModal: () => modalsMock,
}));

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/store/useCanvasStore', () => {
  const storeState = {
    // Fase 4, M7: Salva passa da utils/serverActions -> store.handleSaveQuiet,
    // e solo con modifiche non salvate.
    handleSaveQuiet: handleSaveQuietMock,
    isDirty: true,
    markClean: vi.fn(),
    setEditorActions: vi.fn(),
    setConnections: vi.fn(),
    updateNodeData: vi.fn(),
    // UI-3: i nodi nuovi (catalogo unico, sorgenti) entrano come un blocco.
    addSubgraph: addNodesMock,
    validationHighlights: [],
    nodes: [],
    onNodesChange: vi.fn(),
    lastValidationSummary: null,
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
    await userEvent.click(screen.getByRole('button', { name: /^salva$/i }));

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
        metadata: { name: 'Arricchisci Orders', updated_at: '2026-11-04T10:00:00Z' },
        // Versione del canvas del GET: base del salvataggio condizionato.
        canvas_version: 'c1:abc',
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
      expect(loadGraphMock).toHaveBeenCalledWith(loadedNodes, [
        {
          id: 'e-1',
          source: 'raw-1',
          target: 'result-1',
          sourceHandle: 'output',
          targetHandle: 'input',
        },
      ], { canvasVersion: 'c1:abc', updatedAt: '2026-11-04T10:00:00Z' });
    });
  });

  it('adds a project source node from the canvas drop payload and then saves through the current editor state', async () => {
    const { container } = render(<ArricchisciEditor />);
    expect(await screen.findByText('Arricchisci Orders')).toBeInTheDocument();

    const canvas = container.querySelector('.arricchisci-canvas');
    const dataTransfer = {
      // Formato unico del trascinamento di una sorgente (UI-3, editorNodeCatalog).
      getData: vi.fn((key) => (key === 'application/x-plenora-source'
        ? JSON.stringify({ kind: 'project_source', item: { id: 'proj-upstream-1', name: 'Ordini a monte' } })
        : '')),
      dropEffect: 'move',
    };

    fireEvent.drop(canvas, {
      dataTransfer,
      clientX: 320,
      clientY: 180,
    });

    // Azione utente esplicita: un blocco con il nodo, selezionato (audit R1).
    expect(addNodesMock).toHaveBeenCalled();
    const nextNodes = addNodesMock.mock.calls.at(-1)[0].nodes;
    expect(nextNodes).toHaveLength(1);
    // U9: il nodo porta anche il nome del progetto (mai l'id come titolo).
    expect(nextNodes[0]).toMatchObject({
      type: 'project_source',
      data: {
        source_project_id: 'proj-upstream-1',
        project_name: 'Ordini a monte',
        columns: [],
      },
    });

    await userEvent.click(screen.getByRole('button', { name: /^salva$/i }));

    await waitFor(() => {
      expect(handleSaveQuietMock).toHaveBeenCalledTimes(1);
    });
  });


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
