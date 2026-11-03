import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import EditorShell from '../../../src/components/editor/EditorShell';

/**
 * Contract test per EditorShell (FE-SPLIT-004 step 1).
 *
 * Verifica le 3 invarianti:
 *   1. Guard branches: invalid projectId -> redirect '/'; loading ->
 *      LoadingOverlay; loadError -> banner Riprova/Torna; project null
 *      -> banner Torna.
 *   2. Render normale: topbar/sidebar/beforeCanvas/afterCanvas/modals
 *      passati come slot vengono renderizzati nel layout corretto.
 *   3. Canvas wrapper: ReactFlow viene renderizzato dentro
 *      CanvasErrorBoundary con Background/Controls/MiniMap (mockati).
 *      isReadOnly=true aggiunge la classe `canvas-readonly`.
 */

const { navigateMock, reactFlowStateRef, reactFlowPropsRef } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  reactFlowStateRef: { current: { nodes: [], edges: [] } },
  reactFlowPropsRef: { current: null },
}));

vi.mock('@xyflow/react', () => {
  const React = require('react');
  const base = globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef: reactFlowStateRef });
  // Wrappiamo il componente ReactFlow (export con nome in v12) per catturare
  // le props passate (test edgeTypes).
  const capture = (props) => {
    reactFlowPropsRef.current = props;
    return base.ReactFlow(props);
  };
  return { ...base, ReactFlow: capture, default: capture };
});

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../../src/utils/validateRouteParam', () => ({
  isValidProjectId: (value) => value === 'proj-1',
}));

vi.mock('../../../src/components/common/LoadingOverlay', () => ({
  LoadingOverlay: ({ message }) => <div data-testid="loading">{message}</div>,
}));

vi.mock('../../../src/components/common/ErrorBoundary', () => ({
  CanvasErrorBoundary: ({ children }) => (
    <div data-testid="canvas-error-boundary">{children}</div>
  ),
}));

const baseProps = {
  projectId: 'proj-1',
  prefix: 'estrai',
  loading: false,
  loadError: null,
  project: { metadata: { name: 'Estrai Orders' }, canvas: { nodes: [], edges: [] } },
  reload: vi.fn(),
  onBack: vi.fn(),
  nodes: [],
  edges: [],
  canvasConfig: {
    nodeTypes: {},
    isReadOnly: false,
  },
  canvasHandlers: {
    onNodesChange: vi.fn(),
    onEdgesChange: vi.fn(),
  },
  slots: {},
};

describe('EditorShell — contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    reactFlowStateRef.current = { nodes: [], edges: [] };
  });

  it('redirects to home and renders nothing when projectId is invalid', async () => {
    globalThis.__frontendConsolePolicy.allowError = true;
    render(<EditorShell {...baseProps} projectId="bad-id" />);
    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith('/', { replace: true });
    });
    expect(screen.queryByTestId('loading')).not.toBeInTheDocument();
    expect(screen.queryByTestId('canvas-error-boundary')).not.toBeInTheDocument();
  });

  it('renders the LoadingOverlay when loading=true', () => {
    render(<EditorShell {...baseProps} loading={true} />);
    expect(screen.getByTestId('loading')).toHaveTextContent('Caricamento progetto...');
    expect(screen.queryByTestId('canvas-error-boundary')).not.toBeInTheDocument();
  });

  it('renders the load-error banner with role="alert", aria-live, and initial focus on first action', async () => {
    const reload = vi.fn();
    const onBack = vi.fn();
    render(
      <EditorShell
        {...baseProps}
        loadError="DB unreachable"
        reload={reload}
        onBack={onBack}
      />,
    );
    expect(screen.getByText(/Errore caricamento:.*DB unreachable/)).toBeInTheDocument();

    // a11y: role="alert" + aria-live="assertive" sul banner
    const alert = screen.getByRole('alert');
    expect(alert).toHaveAttribute('aria-live', 'assertive');
    // Focus iniziale sul primo bottone (Riprova)
    const retryBtn = screen.getByRole('button', { name: /Riprova/i });
    expect(retryBtn).toHaveFocus();

    await userEvent.click(retryBtn);
    expect(reload).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole('button', { name: /Torna alla lista/i }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('renders the "Progetto non trovato" branch with role="alert", aria-live, and initial focus', async () => {
    const onBack = vi.fn();
    render(<EditorShell {...baseProps} project={null} onBack={onBack} />);
    expect(screen.getByText('Progetto non trovato')).toBeInTheDocument();

    const alert = screen.getByRole('alert');
    expect(alert).toHaveAttribute('aria-live', 'assertive');
    const backBtn = screen.getByRole('button', { name: /Torna alla lista/i });
    expect(backBtn).toHaveFocus();

    await userEvent.click(backBtn);
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('renders all slots (topbar/sidebar/beforeCanvas/afterCanvas/modals) inside the canvas error boundary', () => {
    render(
      <EditorShell
        {...baseProps}
        slots={{
          topbar: <div data-testid="slot-topbar">topbar</div>,
          sidebar: <div data-testid="slot-sidebar">sidebar</div>,
          beforeCanvas: <div data-testid="slot-before">before</div>,
          afterCanvas: <div data-testid="slot-after">after</div>,
          modals: <div data-testid="slot-modals">modals</div>,
          children: <div data-testid="slot-children">children</div>,
        }}
      />,
    );

    expect(screen.getByTestId('slot-topbar')).toBeInTheDocument();
    expect(screen.getByTestId('slot-sidebar')).toBeInTheDocument();
    expect(screen.getByTestId('slot-before')).toBeInTheDocument();
    expect(screen.getByTestId('slot-after')).toBeInTheDocument();
    expect(screen.getByTestId('slot-modals')).toBeInTheDocument();
    expect(screen.getByTestId('slot-children')).toBeInTheDocument();

    // Canvas error boundary contiene il canvas (afterCanvas + children
    // sono renderizzati dentro al canvas wrapper).
    const boundary = screen.getByTestId('canvas-error-boundary');
    expect(boundary).toContainElement(screen.getByTestId('slot-after'));
    expect(boundary).toContainElement(screen.getByTestId('slot-children'));
  });

  it('applies the canvas-readonly class when isReadOnly=true', () => {
    const { container } = render(
      <EditorShell
        {...baseProps}
        canvasConfig={{ ...baseProps.canvasConfig, isReadOnly: true }}
        slots={{ topbar: <div data-testid="slot-topbar">topbar</div> }}
      />,
    );
    const canvasDiv = container.querySelector('.estrai-canvas');
    expect(canvasDiv).toBeTruthy();
    expect(canvasDiv.className).toContain('canvas-readonly');
  });

  it('forwards canvasConfig.edgeTypes to ReactFlow', () => {
    reactFlowPropsRef.current = null;
    const customEdgeTypes = { custom: () => null };
    render(
      <EditorShell
        {...baseProps}
        canvasConfig={{ ...baseProps.canvasConfig, edgeTypes: customEdgeTypes }}
      />,
    );
    expect(reactFlowPropsRef.current).not.toBeNull();
    expect(reactFlowPropsRef.current.edgeTypes).toBe(customEdgeTypes);
  });

  it('uses the prefix prop to namespace root and error CSS classes', () => {
    const { container, rerender } = render(
      <EditorShell {...baseProps} prefix="manipola" />,
    );
    expect(container.querySelector('.manipola-editor')).toBeTruthy();
    expect(container.querySelector('.manipola-canvas')).toBeTruthy();

    rerender(<EditorShell {...baseProps} prefix="arricchisci" loadError="boom" />);
    expect(container.querySelector('.arricchisci-error')).toBeTruthy();
  });
});
