/**
 * Contract test — ResultNode (parent orchestrator).
 *
 * Esistono già contract test estensivi per i due figli:
 *   - ResultNodeStandardView.contract.test.jsx
 *   - ResultNodeImdfView.contract.test.jsx
 * e un test sul ResultNode parent (ResultNode.contract.test.jsx) che copre
 * SOLO il percorso IMDF "happy path".
 *
 * Questo file copre i gap reali a livello PARENT:
 *   1. Branch selection: upstream non api_enrich → StandardView, non ImdfView.
 *   2. Validation gate su Execute: validazione fallita → onExecute NON chiamato
 *      + ValidationModal aperta.
 *   3. Validation gate su Preview: idem per onPreview.
 *   4. Nessuna eliminazione propria: intestazione, × ed "Elimina passo"
 *      sono del pannello (NodeInspector, regola 6 del Blocco A; vedi
 *      InspectorShell.contract.test.jsx).
 *   5. handleValidate/Execute/Preview validano via BACKEND (Epic CanvasVal §12):
 *      valida la versione salvata (Esegui salva prima; Valida chiede se ci
 *      sono modifiche non salvate, Fase 4 M7). Niente più
 *      validazione JS per-famiglia: il backend è la single source of truth.
 *
 * Nota: la validazione e' invocata dal parent (handleExecuteWithValidation /
 * handleValidate), non dal child standard. Quindi e' un contratto del parent.
 */

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const {
  onPreviewMock,
  onExecuteMock,
  updateNodeDataMock,
  deleteNodeMock,
  setLastTaskResultMock,
  useCanvasStoreMock,
  canvasStateRef,
  validateProjectCanvasMock,
  handleSaveQuietMock,
  useConfirmMock,
  listImdfBundlesMock,
  getImdfBundleMetadataMock,
  deleteImdfBundleMock,
  setValidationHighlightsMock,
  setLastValidationSummaryMock,
  setCanvasViewMock,
} = vi.hoisted(() => ({
  onPreviewMock: vi.fn(),
  onExecuteMock: vi.fn(),
  updateNodeDataMock: vi.fn(),
  deleteNodeMock: vi.fn(),
  setLastTaskResultMock: vi.fn(),
  useCanvasStoreMock: vi.fn(),
  canvasStateRef: { current: { nodes: [], edges: [] } },
  validateProjectCanvasMock: vi.fn(),
  handleSaveQuietMock: vi.fn(),
  useConfirmMock: vi.fn(),
  listImdfBundlesMock: vi.fn(),
  getImdfBundleMetadataMock: vi.fn(),
  deleteImdfBundleMock: vi.fn(),
  setValidationHighlightsMock: vi.fn(),
  setLastValidationSummaryMock: vi.fn(),
  setCanvasViewMock: vi.fn(),
}));

vi.mock('@xyflow/react', () =>
  globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef, showRoot: false })
);

vi.mock('../../src/store/useCanvasStore', () => ({
  default: useCanvasStoreMock,
}));

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => ({
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

// Backend validation: single source of truth (Epic CanvasVal §12). Preserviamo
// gli altri export di api/projects (usati transitivamente dalla view).
vi.mock('../../src/api/projects', async (importOriginal) => ({
  ...(await importOriginal()),
  validateProjectCanvas: validateProjectCanvasMock,
}));

vi.mock('../../src/utils/columnUtils', () => ({
  getColumnTypeIcon: vi.fn(() => 'A'),
  getColumnTypeLabel: vi.fn(() => 'text'),
  getColumnSRID: vi.fn(() => null),
  isGeometryType: vi.fn(() => false),
}));

// useConfirm: mock totale per controllare l'esito del dialog.
vi.mock('../../src/context/ConfirmContext', () => ({
  ConfirmProvider: ({ children }) => children,
  useConfirm: () => useConfirmMock,
}));

vi.mock('../../src/api/imdf', () => ({
  listImdfBundles: listImdfBundlesMock,
  getImdfBundleMetadata: getImdfBundleMetadataMock,
  deleteImdfBundle: deleteImdfBundleMock,
}));

vi.mock('../../src/components/modals/ValidationModal', () => ({
  default: ({ isOpen, onFocusNode, result }) => {
    if (!isOpen) return null;
    const issues = result?.issues || [];
    return (
      <div data-testid="validation-modal">
        {issues.map((issue, i) => (
          <div
            key={i}
            data-testid={issue.nodeId ? `issue-row-${issue.nodeId}` : `issue-row-${i}`}
            onClick={issue.nodeId && onFocusNode ? () => onFocusNode(issue.nodeId) : undefined}
          >
            {issue.message}
          </div>
        ))}
      </div>
    );
  },
}));

vi.mock('../../src/components/modals/RunHistoryModal', () => ({
  default: ({ isOpen }) => (isOpen ? <div data-testid="run-history-modal" /> : null),
}));

// Modali IMDF: no-op (non renderizzati nel ramo standard).
vi.mock('../../src/components/modals/ImdfPreviewModal', () => ({ default: () => null }));
vi.mock('../../src/components/modals/ImdfManifestModal', () => ({ default: () => null }));
vi.mock('../../src/components/modals/ImdfValidationModal', () => ({ default: () => null }));
vi.mock('../../src/components/modals/ImdfBundlesManagerModal', () => ({ default: () => null }));
vi.mock('../../src/components/imdf/BundleSlotCard', () => ({ default: () => null }));

import ResultNode from '../../src/components/nodes/ResultNode';
import PipelineRunner from '../../src/components/editor/PipelineRunner';

// Helper: risposta backend POST /validate ({valid, has_errors, issues:[{node_id,severity,code,message}]}).
const backendReport = (issues = []) => ({
  valid: !issues.some(i => i.severity === 'error'),
  has_errors: issues.some(i => i.severity === 'error'),
  issues,
});

// Le azioni registrate (PipelineRunner, editor) vivono nel ref come nello
// store vero: setEditorActions le fonde, getEditorAction le legge.
const buildCanvasState = ({ extraActions = {}, ...overrides } = {}) => {
  const editorActionsRef = { current: { onPreview: onPreviewMock, onExecute: onExecuteMock, ...extraActions } };
  return {
  setEditorActions: (actions) => Object.assign(editorActionsRef.current, actions),
  getEditorAction: (name) => editorActionsRef.current[name],
  editorActionsRef,
  updateNodeData: updateNodeDataMock,
  deleteNode: deleteNodeMock,
  lastTaskResult: null,
  setLastTaskResult: setLastTaskResultMock,
  setValidationHighlights: setValidationHighlightsMock,
  setLastValidationSummary: setLastValidationSummaryMock,
  handleSaveQuiet: handleSaveQuietMock,
  // Fase 4, M7: stato di esecuzione condiviso e opzioni nello store.
  runState: 'idle',
  // "Vai al nodo" usa la messa a fuoco della vista aperta (canvas o Passi).
  canvasView: 'steps',
  setCanvasView: setCanvasViewMock,
  setRunState: vi.fn(),
  runOptions: { force_recalculate: false },
  setRunReady: vi.fn(),
  isDirty: false,
  isReadOnly: false,
  projectId: 'proj-1',
  getState: vi.fn(),
  nodes: [
    { id: 'src-1', type: 'tableNode', data: { columns: [] } },
    { id: 'result-1', type: 'result', data: {} },
  ],
  edges: [{ id: 'e1', source: 'src-1', target: 'result-1' }],
  transformsRegistry: null,
  ...overrides,
  };
};

const setCanvasState = (state) => {
  canvasStateRef.current = state;
  useCanvasStoreMock.mockImplementation((selector) => selector(state));
  useCanvasStoreMock.getState = () => state;
};

beforeEach(() => {
  vi.clearAllMocks();
  validateProjectCanvasMock.mockResolvedValue(backendReport([]));
  handleSaveQuietMock.mockResolvedValue(undefined);
  useConfirmMock.mockResolvedValue(true);
  listImdfBundlesMock.mockResolvedValue([]);
  getImdfBundleMetadataMock.mockResolvedValue({});
  deleteImdfBundleMock.mockResolvedValue(undefined);
  setCanvasState(buildCanvasState());
});

// Il percorso unico (Valida/Esegui/Anteprima) e' PipelineRunner, montato
// dall'editor accanto al canvas (Fase 4, M7): qui come nell'editor.
const renderResultNode = (data = {}) =>
  render(
    <>
    <PipelineRunner />
    <ResultNode
      id="result-1"
      data={{
        fileName: 'output',
        format: 'csv',
        columns: [],
        availableColumns: [],
        project_id: 'proj-1',
        ...data,
      }}
    />
    </>
  );

describe('ResultNode (parent) — branch & gating contract', () => {
  it('upstream non-IMDF: monta StandardView (filename + Esegui, come la barra), non IMDF', () => {
    renderResultNode();

    expect(screen.getByText('output.csv')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Esegui' })).toBeInTheDocument();

    expect(screen.queryByRole('button', { name: /visualizza imdf/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /visualizza manifest/i })).toBeNull();
  });

  it('Execute con validazione FALLITA: onExecute NON chiamato + ValidationModal aperta', async () => {
    validateProjectCanvasMock.mockResolvedValue(backendReport([
      { node_id: '', severity: 'error', code: 'CARICA_NOT_CONNECTED', message: 'connessione mancante' },
    ]));

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: 'Esegui' }));

    await waitFor(() => {
      expect(screen.getByTestId('validation-modal')).toBeInTheDocument();
    });
    expect(onExecuteMock).not.toHaveBeenCalled();
    expect(validateProjectCanvasMock).toHaveBeenCalledWith('proj-1');
  });

  it('Preview con validazione FALLITA: onPreview NON chiamato + ValidationModal aperta', async () => {
    validateProjectCanvasMock.mockResolvedValue(backendReport([
      { node_id: '', severity: 'error', code: 'structure.dangling_edge', message: 'edge mancante' },
    ]));

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: 'Anteprima' }));

    await waitFor(() => {
      expect(screen.getByTestId('validation-modal')).toBeInTheDocument();
    });
    expect(onPreviewMock).not.toHaveBeenCalled();
  });

  it('nessuna intestazione ne\' eliminazione propria: le possiede il pannello', () => {
    const { container } = renderResultNode();
    expect(container.querySelector('[class$="__header"]')).toBeNull();
    expect(screen.queryByTitle('Elimina nodo')).toBeNull();
    expect(screen.queryByRole('button', { name: '×' })).toBeNull();
    expect(deleteNodeMock).not.toHaveBeenCalled();
  });

  it('handleValidate: valida la versione salvata via backend, senza salvare di nascosto (M7)', async () => {
    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: /valida/i }));

    await waitFor(() => {
      expect(validateProjectCanvasMock).toHaveBeenCalledWith('proj-1');
    });
    // Canvas pulito: nessun salvataggio (utils/serverActions).
    expect(handleSaveQuietMock).not.toHaveBeenCalled();
  });

  it('handleValidate con modifiche non salvate: il pulsante dice «Salva e valida», salva poi valida (UI-3, U15)', async () => {
    setCanvasState(buildCanvasState({ isDirty: true }));
    const user = userEvent.setup();
    renderResultNode();
    await user.click(screen.getByRole('button', { name: /Salva e valida/ }));
    await waitFor(() => expect(validateProjectCanvasMock).toHaveBeenCalledWith('proj-1'));
    expect(handleSaveQuietMock.mock.invocationCallOrder[0]).toBeLessThan(validateProjectCanvasMock.mock.invocationCallOrder[0]);
  });

  it('Esegui con risorse non accessibili: bloccato PRIMA di salvare, nodi evidenziati', async () => {
    setCanvasState(buildCanvasState({
      nodes: [{ id: 'src-1', type: 'tableNode', data: { columns: [], access_denied: true } }, { id: 'result-1', type: 'result', data: {} }],
    }));
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const user = userEvent.setup();
    renderResultNode();
    await user.click(screen.getByRole('button', { name: /esegui/i }));
    expect(await screen.findByTestId('validation-modal')).toBeTruthy();
    expect(handleSaveQuietMock).not.toHaveBeenCalled();
    expect(onExecuteMock).not.toHaveBeenCalled();
    expect(setValidationHighlightsMock).toHaveBeenCalledWith(['src-1']);
  });

  it('handleValidate: variante MANIPOLA usa lo stesso backend (niente dispatch JS)', async () => {
    const user = userEvent.setup();
    renderResultNode({ isManipulate: true });

    await user.click(screen.getByRole('button', { name: /valida/i }));

    await waitFor(() => {
      expect(validateProjectCanvasMock).toHaveBeenCalledWith('proj-1');
    });
  });

  it('handleValidate: variante ARRICCHISCI usa lo stesso backend', async () => {
    const user = userEvent.setup();
    renderResultNode({ isArricchisci: true });

    await user.click(screen.getByRole('button', { name: /valida/i }));

    await waitFor(() => {
      expect(validateProjectCanvasMock).toHaveBeenCalledWith('proj-1');
    });
  });

  it('Click-to-focus: click su issue con nodeId chiude il modal', async () => {
    validateProjectCanvasMock.mockResolvedValue(backendReport([
      { node_id: 'src-1', severity: 'error', code: 'SOURCE_ISOLATED', message: 'Nodo isolato' },
    ]));

    setCanvasState(buildCanvasState({
      nodes: [
        { id: 'src-1', type: 'tableNode', position: { x: 100, y: 200 }, data: { columns: [] } },
        { id: 'result-1', type: 'result', position: { x: 400, y: 200 }, data: {} },
      ],
    }));

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: 'Esegui' }));
    await waitFor(() => {
      expect(screen.getByTestId('validation-modal')).toBeInTheDocument();
    });

    await user.click(screen.getByTestId('issue-row-src-1'));

    await waitFor(() => {
      expect(screen.queryByTestId('validation-modal')).not.toBeInTheDocument();
    expect(setCanvasViewMock).toHaveBeenCalledWith('steps', expect.any(String));
    });
  });

  it('Issue row senza nodeId: nessun onClick', async () => {
    validateProjectCanvasMock.mockResolvedValue(backendReport([
      { node_id: '', severity: 'error', code: 'CANVAS_EMPTY', message: 'Canvas vuoto' },
    ]));

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: 'Esegui' }));
    await waitFor(() => {
      expect(screen.getByTestId('validation-modal')).toBeInTheDocument();
    });

    const issueRow = screen.getByTestId('issue-row-0');
    expect(issueRow).toBeInTheDocument();

    await user.click(issueRow);
    expect(screen.getByTestId('validation-modal')).toBeInTheDocument();
  });

  it('Validazione FALLITA: setValidationHighlights chiamato con i nodeId delle issue', async () => {
    validateProjectCanvasMock.mockResolvedValue(backendReport([
      { node_id: 'src-1', severity: 'error', code: 'SOURCE_ISOLATED', message: 'Nodo isolato' },
      { node_id: '', severity: 'error', code: 'CANVAS_EMPTY', message: 'Canvas vuoto' },
    ]));

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: 'Esegui' }));

    await waitFor(() => {
      expect(setValidationHighlightsMock).toHaveBeenCalledWith(['src-1']);
    });
  });

  it('Validazione OK: setValidationHighlights chiamato con array vuoto', async () => {
    validateProjectCanvasMock.mockResolvedValue(backendReport([]));

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: 'Esegui' }));

    await waitFor(() => {
      expect(setValidationHighlightsMock).toHaveBeenCalledWith([]);
    });
  });

  it('Validazione FALLITA: setLastValidationSummary chiamato con conteggi severity', async () => {
    validateProjectCanvasMock.mockResolvedValue(backendReport([
      { node_id: 'src-1', severity: 'error', code: 'SOURCE_ISOLATED', message: 'Nodo isolato' },
      { node_id: '', severity: 'error', code: 'CANVAS_EMPTY', message: 'Canvas vuoto' },
      { node_id: '', severity: 'warning', code: 'WARN_1', message: 'Avviso' },
    ]));

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: 'Esegui' }));

    await waitFor(() => {
      expect(setLastValidationSummaryMock).toHaveBeenCalledWith({
        errorCount: 2,
        warningCount: 1,
        infoCount: 0,
      });
    });
  });
});
