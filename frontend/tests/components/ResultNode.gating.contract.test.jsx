/**
 * Contract test — ResultNode (parent orchestrator).
 *
 * Esistono già contract test estensivi per i due figli:
 *   - ResultNodeStandardView.contract.test.jsx
 *   - ResultNodeImdfView.contract.test.jsx
 * e un test sul ResultNode parent (ResultNode.contract.test.jsx) che copre
 * SOLO il percorso IMDF "happy path" (hydration + manager + execute).
 *
 * Questo file copre i gap reali a livello PARENT, ad alto valore anti-regressione:
 *
 *   1. Branch selection: con upstream non api_enrich (no IMDF) il parent
 *      monta ResultNodeStandardView, NON ResultNodeImdfView.
 *   2. Validation gate su Execute (standard mode): se la validazione fallisce
 *      `editorActionsRef.current.onExecute` NON viene chiamato e si apre
 *      ValidationModal.
 *   3. Validation gate su Preview (standard mode): idem per onPreview.
 *   4. Delete cancellato: se l'utente annulla il confirm, deleteNode NON
 *      viene chiamato. Il path "confermato → deleteNode chiamato" e' coperto
 *      indirettamente; aggiungiamo l'inverso che e' la regressione tipica.
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
  validateEstraiCanvasMock,
  validateManipolaCanvasMock,
  validateArricchisciCanvasMock,
  useConfirmMock,
  listImdfBundlesMock,
  getImdfBundleMetadataMock,
  deleteImdfBundleMock,
  listApiConnectionsMock,
  setValidationHighlightsMock,
} = vi.hoisted(() => ({
  onPreviewMock: vi.fn(),
  onExecuteMock: vi.fn(),
  updateNodeDataMock: vi.fn(),
  deleteNodeMock: vi.fn(),
  setLastTaskResultMock: vi.fn(),
  useCanvasStoreMock: vi.fn(),
  canvasStateRef: { current: { nodes: [], edges: [] } },
  validateEstraiCanvasMock: vi.fn(),
  validateManipolaCanvasMock: vi.fn(),
  validateArricchisciCanvasMock: vi.fn(),
  useConfirmMock: vi.fn(),
  listImdfBundlesMock: vi.fn(),
  getImdfBundleMetadataMock: vi.fn(),
  deleteImdfBundleMock: vi.fn(),
  listApiConnectionsMock: vi.fn(),
  setValidationHighlightsMock: vi.fn(),
}));

vi.mock('reactflow', () =>
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

vi.mock('../../src/utils/validateEstraiCanvas', () => ({
  validateEstraiCanvas: validateEstraiCanvasMock,
}));
vi.mock('../../src/utils/validateManipolaCanvas', () => ({
  validateManipolaCanvas: validateManipolaCanvasMock,
}));
vi.mock('../../src/utils/validateArricchisciCanvas', () => ({
  validateArricchisciCanvas: validateArricchisciCanvasMock,
}));

vi.mock('../../src/utils/columnUtils', () => ({
  getColumnTypeIcon: vi.fn(() => 'A'),
  getColumnTypeLabel: vi.fn(() => 'text'),
  getColumnSRID: vi.fn(() => null),
  isGeometryType: vi.fn(() => false),
}));

// useConfirm: mock totale per controllare l'esito del dialog senza
// renderizzare ConfirmProvider/ConfirmDialog.
vi.mock('../../src/context/ConfirmContext', () => ({
  ConfirmProvider: ({ children }) => children,
  useConfirm: () => useConfirmMock,
}));

vi.mock('../../src/api/imdf', () => ({
  listImdfBundles: listImdfBundlesMock,
  getImdfBundleMetadata: getImdfBundleMetadataMock,
  deleteImdfBundle: deleteImdfBundleMock,
}));

vi.mock('../../src/api/apiConnections', () => ({
  listApiConnections: listApiConnectionsMock,
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

// Modali IMDF: mockati come no-op; non vengono renderizzati nel ramo standard
// ma sono comunque importati transitorialmente da ResultNodeImdfView.
vi.mock('../../src/components/modals/ImdfPreviewModal', () => ({ default: () => null }));
vi.mock('../../src/components/modals/ImdfManifestModal', () => ({ default: () => null }));
vi.mock('../../src/components/modals/ImdfValidationModal', () => ({ default: () => null }));
vi.mock('../../src/components/modals/ImdfBundlesManagerModal', () => ({ default: () => null }));
vi.mock('../../src/components/imdf/BundleSlotCard', () => ({ default: () => null }));

import ResultNode from '../../src/components/nodes/ResultNode';

const buildCanvasState = (overrides = {}) => ({
  updateNodeData: updateNodeDataMock,
  deleteNode: deleteNodeMock,
  lastTaskResult: null,
  setLastTaskResult: setLastTaskResultMock,
  setValidationHighlights: setValidationHighlightsMock,
  projectId: 'proj-1',
  editorActionsRef: {
    current: {
      onPreview: onPreviewMock,
      onExecute: onExecuteMock,
    },
  },
  getState: vi.fn(),
  nodes: [
    // upstream STANDARD (non api_enrich): forza isImdfMode=false
    { id: 'src-1', type: 'tableNode', data: { columns: [] } },
    { id: 'result-1', type: 'result', data: {} },
  ],
  edges: [{ id: 'e1', source: 'src-1', target: 'result-1' }],
  transformsRegistry: null,
  ...overrides,
});

const setCanvasState = (state) => {
  canvasStateRef.current = state;
  useCanvasStoreMock.mockImplementation((selector) => selector(state));
  useCanvasStoreMock.getState = () => state;
};

beforeEach(() => {
  vi.clearAllMocks();
  validateEstraiCanvasMock.mockReturnValue({ isValid: true, issues: [] });
  validateManipolaCanvasMock.mockReturnValue({ isValid: true, issues: [] });
  validateArricchisciCanvasMock.mockReturnValue({ isValid: true, issues: [] });
  useConfirmMock.mockResolvedValue(true);
  listImdfBundlesMock.mockResolvedValue([]);
  getImdfBundleMetadataMock.mockResolvedValue({});
  deleteImdfBundleMock.mockResolvedValue(undefined);
  listApiConnectionsMock.mockResolvedValue({ api_connections: [] });
  setCanvasState(buildCanvasState());
});

const renderResultNode = (data = {}) =>
  render(
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
  );

describe('ResultNode (parent) — branch & gating contract', () => {
  it('upstream non-IMDF: monta StandardView (filename + Esegui Query), non IMDF', () => {
    renderResultNode();

    // Standard view markers
    expect(screen.getByText('output.csv')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /esegui query/i })).toBeInTheDocument();

    // IMDF view markers ASSENTI
    expect(screen.queryByRole('button', { name: /visualizza imdf/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /visualizza manifest/i })).toBeNull();
  });

  it('Execute con validazione FALLITA: onExecute NON chiamato + ValidationModal aperta', async () => {
    validateEstraiCanvasMock.mockReturnValue({
      isValid: false,
      issues: [{ message: 'connessione mancante' }],
    });

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: /esegui query/i }));

    // Aspettiamo l'apertura della modale (handleExecuteWithValidation e' async).
    await waitFor(() => {
      expect(screen.getByTestId('validation-modal')).toBeInTheDocument();
    });
    expect(onExecuteMock).not.toHaveBeenCalled();
    expect(validateEstraiCanvasMock).toHaveBeenCalled();
  });

  it('Preview con validazione FALLITA: onPreview NON chiamato + ValidationModal aperta', async () => {
    validateEstraiCanvasMock.mockReturnValue({
      isValid: false,
      issues: [{ message: 'edge mancante' }],
    });

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: /preview/i }));

    expect(screen.getByTestId('validation-modal')).toBeInTheDocument();
    expect(onPreviewMock).not.toHaveBeenCalled();
  });

  it('Delete cancellato dal confirm: deleteNode NON chiamato', async () => {
    useConfirmMock.mockResolvedValue(false);

    const user = userEvent.setup();
    renderResultNode();

    // Il bottone delete ha textContent "×" che copre l'accessible-name; uso il title.
    await user.click(screen.getByTitle('Elimina nodo'));

    // Attendi la promise del confirm
    await waitFor(() => {
      expect(useConfirmMock).toHaveBeenCalledTimes(1);
    });
    expect(deleteNodeMock).not.toHaveBeenCalled();
  });

  it('handleValidate dispatch: variante MANIPOLA usa validateManipolaCanvas (non Estrai)', async () => {
    const user = userEvent.setup();
    renderResultNode({ isManipulate: true });

    await user.click(screen.getByRole('button', { name: /valida/i }));

    expect(validateManipolaCanvasMock).toHaveBeenCalledTimes(1);
    expect(validateEstraiCanvasMock).not.toHaveBeenCalled();
    expect(validateArricchisciCanvasMock).not.toHaveBeenCalled();
  });

  it('handleValidate dispatch: variante ARRICCHISCI usa validateArricchisciCanvas', async () => {
    const user = userEvent.setup();
    renderResultNode({ isArricchisci: true });

    await user.click(screen.getByRole('button', { name: /valida/i }));

    await waitFor(() => {
      expect(validateArricchisciCanvasMock).toHaveBeenCalledTimes(1);
    });
    expect(validateEstraiCanvasMock).not.toHaveBeenCalled();
    expect(validateManipolaCanvasMock).not.toHaveBeenCalled();
  });

  it('Click-to-focus: click su issue con nodeId chiude il modal', async () => {
    validateEstraiCanvasMock.mockReturnValue({
      isValid: false,
      issues: [
        { id: 'SOURCE_ISOLATED', severity: 'error', message: 'Nodo isolato', nodeId: 'src-1', nodeName: 'src-1' },
      ],
      ok: false,
      bySeverity: {
        error: [{ id: 'SOURCE_ISOLATED', severity: 'error', message: 'Nodo isolato', nodeId: 'src-1', nodeName: 'src-1' }],
        warning: [],
        info: [],
      },
    });

    // Assicura che getNodes trovi il nodo con una posizione
    setCanvasState(buildCanvasState({
      nodes: [
        { id: 'src-1', type: 'tableNode', position: { x: 100, y: 200 }, data: { columns: [] } },
        { id: 'result-1', type: 'result', position: { x: 400, y: 200 }, data: {} },
      ],
    }));

    const user = userEvent.setup();
    renderResultNode();

    // Trigger validazione fallita → apre modal
    await user.click(screen.getByRole('button', { name: /esegui query/i }));
    await waitFor(() => {
      expect(screen.getByTestId('validation-modal')).toBeInTheDocument();
    });

    // Click sulla issue row con nodeId
    await user.click(screen.getByTestId('issue-row-src-1'));

    // Il modal si deve chiudere (handleFocusNode chiama setShowValidation(false))
    await waitFor(() => {
      expect(screen.queryByTestId('validation-modal')).not.toBeInTheDocument();
    });
  });

  it('Issue row senza nodeId: nessun onClick', async () => {
    validateEstraiCanvasMock.mockReturnValue({
      isValid: false,
      issues: [
        { id: 'CANVAS_EMPTY', severity: 'error', message: 'Canvas vuoto' },
      ],
      ok: false,
      bySeverity: {
        error: [{ id: 'CANVAS_EMPTY', severity: 'error', message: 'Canvas vuoto' }],
        warning: [],
        info: [],
      },
    });

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: /esegui query/i }));
    await waitFor(() => {
      expect(screen.getByTestId('validation-modal')).toBeInTheDocument();
    });

    // La issue row senza nodeId non ha data-testid con nodeId
    const issueRow = screen.getByTestId('issue-row-0');
    expect(issueRow).toBeInTheDocument();

    // Click sulla row: il modal resta aperto (nessun onFocusNode invocato)
    await user.click(issueRow);
    expect(screen.getByTestId('validation-modal')).toBeInTheDocument();
  });

  it('Validazione FALLITA: setValidationHighlights chiamato con i nodeId delle issue', async () => {
    validateEstraiCanvasMock.mockReturnValue({
      isValid: false,
      issues: [
        { id: 'SOURCE_ISOLATED', severity: 'error', message: 'Nodo isolato', nodeId: 'src-1' },
        { id: 'CANVAS_EMPTY', severity: 'error', message: 'Canvas vuoto' },
      ],
    });

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: /esegui query/i }));

    await waitFor(() => {
      expect(setValidationHighlightsMock).toHaveBeenCalledWith(['src-1']);
    });
  });

  it('Validazione OK: setValidationHighlights chiamato con array vuoto', async () => {
    validateEstraiCanvasMock.mockReturnValue({
      isValid: true,
      issues: [],
    });

    const user = userEvent.setup();
    renderResultNode();

    await user.click(screen.getByRole('button', { name: /esegui query/i }));

    await waitFor(() => {
      expect(setValidationHighlightsMock).toHaveBeenCalledWith([]);
    });
  });
});
