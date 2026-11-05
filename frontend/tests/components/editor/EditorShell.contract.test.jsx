import fs from 'node:fs';
import path from 'node:path';
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

  it('uses the prefix prop only for the per-editor canvas class', () => {
    const { container, rerender } = render(
      <EditorShell {...baseProps} prefix="manipola" />,
    );
    expect(container.querySelector('.editor-shell')).toBeTruthy();
    expect(container.querySelector('.editor-shell__canvas.manipola-canvas')).toBeTruthy();

    rerender(<EditorShell {...baseProps} prefix="arricchisci" loadError="boom" />);
    expect(container.querySelector('.editor-shell__error')).toBeTruthy();
  });
});

// Fase 5 T7, correzione dopo la conferma: T7 aveva tolto le regole di
// `${prefix}-editor/-main/-canvas...` (composte a runtime) e i tre editor
// erano senza impaginazione. jsdom non calcola il layout: il test legge il
// CSS e verifica che OGNI classe messa sui contenitori del guscio abbia le
// regole di impaginazione attese (classi statiche, una sola fonte).
describe('EditorShell — impaginazione dei contenitori (CSS letto dal file)', () => {
  const css = (rel) => fs.readFileSync(path.resolve(import.meta.dirname, '../../../src', rel), 'utf-8')
    .replace(/\/\*[\s\S]*?\*\//g, '');
  const SHELL_CSS = css('components/editor/EditorShell.css');
  const EDITOR_CSS = {
    estrai: css('styles/EstraiEditor.css'),
    manipola: css('styles/ManipolaEditor.css'),
    arricchisci: css('styles/ArricchisciEditor.css'),
  };
  // Dichiarazioni della regola il cui selettore è ESATTAMENTE `.cls`.
  const decls = (text, cls) => {
    const out = {};
    for (const [, sel, body] of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (!sel.split(',').map(x => x.trim()).includes(`.${cls}`)) continue;
      for (const d of body.split(';')) {
        const i = d.indexOf(':');
        if (i > 0) out[d.slice(0, i).trim()] = d.slice(i + 1).trim();
      }
    }
    return out;
  };
  const EXPECTED = {
    'editor-shell': { height: '100vh', display: 'flex', 'flex-direction': 'column' },
    'editor-shell__main': { flex: '1', display: 'flex', 'min-height': '0' },
    'editor-shell__canvas': { flex: '1', height: '100%', position: 'relative' },
    'editor-shell__hex': { position: 'absolute', 'pointer-events': 'none' },
    'editor-shell__error': { display: 'flex', 'flex-direction': 'column', 'align-items': 'center' },
    'editor-shell__error-message': { 'font-size': '15px' },
    'editor-shell__error-btn': { cursor: 'pointer' },
  };

  it.each(['estrai', 'manipola', 'arricchisci'])('%s: i contenitori renderizzati hanno le regole del guscio', (prefix) => {
    const hexCanvasRef = React.createRef();
    const { container, unmount } = render(<EditorShell {...baseProps} prefix={prefix} hexCanvasRef={hexCanvasRef} />);
    const root = container.firstElementChild;
    const main = container.querySelector('.editor-shell__main');
    const canvas = container.querySelector('.editor-shell__canvas');
    const hex = container.querySelector('canvas');
    expect(root.classList.contains('editor-shell')).toBe(true);
    expect(main.parentElement).toBe(root);
    expect(main.contains(canvas)).toBe(true);
    expect(hex.parentElement).toBe(canvas);
    expect(canvas.classList.contains(`${prefix}-canvas`)).toBe(true);
    // Ogni classe dei contenitori ha una regola nel CSS (guscio o editor).
    for (const el of [root, main, canvas, hex]) {
      for (const cls of el.classList) {
        const where = /^editor-shell/.test(cls) ? SHELL_CSS : EDITOR_CSS[prefix];
        expect({ cls, rule: new RegExp(`\.${cls}(?![\w-])`).test(where) }).toEqual({ cls, rule: true });
      }
    }
    for (const [cls, want] of Object.entries(EXPECTED)) {
      if (!/error/.test(cls)) expect(container.querySelector(`.${cls}`)).toBeTruthy();
      expect({ cls, ...decls(SHELL_CSS, cls) }).toMatchObject({ cls, ...want });
    }
    unmount();
    // Schermata d'errore: stesse classi statiche, stilizzate.
    const err = render(<EditorShell {...baseProps} prefix={prefix} loadError="boom" />);
    for (const cls of ['editor-shell__error', 'editor-shell__error-message', 'editor-shell__error-btn']) {
      expect(err.container.querySelector(`.${cls}`)).toBeTruthy();
    }
    err.unmount();
  });

  it('il CSS del guscio è importato da EditorShell (vale per i tre editor)', () => {
    const src = fs.readFileSync(path.resolve(import.meta.dirname, '../../../src/components/editor/EditorShell.jsx'), 'utf-8');
    expect(src).toMatch(/import '\.\/EditorShell\.css';/);
  });
});
