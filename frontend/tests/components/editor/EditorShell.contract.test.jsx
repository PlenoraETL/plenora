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

  it('uses the prefix prop only for the per-editor variant classes', () => {
    const { container, rerender } = render(
      <EditorShell {...baseProps} prefix="manipola" />,
    );
    expect(container.querySelector('.editor-shell.manipola-editor')).toBeTruthy();
    expect(container.querySelector('.editor-shell__canvas.manipola-canvas')).toBeTruthy();

    rerender(<EditorShell {...baseProps} prefix="arricchisci" loadError="boom" />);
    expect(container.querySelector('.editor-shell__error.arricchisci-error')).toBeTruthy();
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
        // `${prefix}-editor`: aggancio delle varianti, una regola solo se serve.
        if (cls === `${prefix}-editor`) continue;
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

// Correzione finale prima del deploy, punto 3: l'aspetto dei contenitori del
// guscio è quello di 12e33b9d (prima di T7). La tabella OLD è copiata dalle
// regole di 12e33b9d (EstraiEditor.css, ManipolaEditor.css,
// ArricchisciEditor.css: radice, main, canvas, strato esagonale, grafo,
// schermata d'errore) con i valori normalizzati: `var(--x, fallback)` ->
// `var(--x)`, `--ee-*` di Estrai -> il token `--plen-*` che lo definiva,
// `--ee-err` (costante #F87171) e `--ee-radius` (8px) col loro valore.
// Il test applica la cascata (specificità, ordine, !important) ai CSS
// attuali e confronta proprietà per proprietà. Le proprietà in più rispetto
// a 12e33b9d sono solo quelle elencate e motivate in ADDED.
describe('EditorShell — aspetto di 12e33b9d per ciascun editor', () => {
  const read = (rel) => fs.readFileSync(path.resolve(import.meta.dirname, '../../../src', rel), 'utf-8')
    .replace(/\/\*[\s\S]*?\*\//g, '');
  const dropAtRules = (text) => {
    let out = '';
    let i = 0;
    while (i < text.length) {
      const at = text.indexOf('@', i);
      if (at < 0) { out += text.slice(i); break; }
      out += text.slice(i, at);
      let j = text.indexOf('{', at) + 1;
      let depth = 1;
      while (j < text.length && depth > 0) {
        if (text[j] === '{') depth += 1;
        if (text[j] === '}') depth -= 1;
        j += 1;
      }
      i = j;
    }
    return out;
  };
  const stripFallback = (value) => {
    let out = '';
    let i = 0;
    while (i < value.length) {
      if (value.startsWith('var(', i)) {
        let j = i + 4;
        let depth = 1;
        let name = '';
        let inName = true;
        while (j < value.length && depth > 0) {
          const c = value[j];
          if (c === '(') depth += 1;
          if (c === ')') depth -= 1;
          if (c === ',' && depth === 1) inName = false;
          if (inName && depth > 0) name += c;
          j += 1;
        }
        out += `var(${name.trim()})`;
        i = j;
      } else {
        out += value[i];
        i += 1;
      }
    }
    return out;
  };
  const norm = (value) => stripFallback(value).replace(/\s+/g, ' ').trim().replace(/^white$/, '#fff');
  const rules = (text, order) => [...dropAtRules(text).matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([, sel, body], k) => ({
    selectors: sel.split(',').map(x => x.trim()).filter(Boolean),
    decls: body.split(';').map(d => d.trim()).filter(d => d.includes(':')).map(d => {
      const i = d.indexOf(':');
      return [d.slice(0, i).trim(), d.slice(i + 1).trim()];
    }),
    order: order * 10000 + k,
  }));
  const compound = (token) => ({
    tag: (token.match(/^[a-z][\w-]*/) || [null])[0],
    classes: [...token.matchAll(/\.([\w-]+)/g)].map(m => m[1]),
    hover: /:hover/.test(token),
    other: /\[|::|:(?!hover)/.test(token),
  });
  const matches = (c, el) => !c.other && (!c.tag || c.tag === el.tag)
    && c.classes.every(x => el.classes.includes(x));
  // Specificità (classi e :hover = 100, elemento = 1) se il selettore
  // corrisponde all'elemento nello stato dato, altrimenti null.
  const selectorSpec = (selector, el, state) => {
    const parts = selector.split(/\s+/).map(compound);
    const last = parts.pop();
    if (last.hover && state !== 'hover') return null;
    if (!matches(last, el)) return null;
    for (const p of parts) if (p.hover) return null;
    let anc = 0;
    for (const part of [...parts].reverse()) {
      while (anc < el.ancestors.length && !matches(part, el.ancestors[anc])) anc += 1;
      if (anc >= el.ancestors.length) return null;
      anc += 1;
    }
    return [...parts, last].reduce((n, p) => n + p.classes.length * 100 + (p.hover ? 100 : 0) + (p.tag ? 1 : 0), 0);
  };
  const effective = (cssRules, el, state = null) => {
    const winners = {};
    for (const rule of cssRules) {
      const specs = rule.selectors.map(sel => selectorSpec(sel, el, state)).filter(x => x !== null);
      if (!specs.length) continue;
      const spec = Math.max(...specs);
      for (const [prop, raw] of rule.decls) {
        const rank = [/!important/.test(raw) ? 1 : 0, spec, rule.order];
        const prev = winners[prop]?.rank;
        const better = !prev || rank[0] > prev[0] || (rank[0] === prev[0] && (rank[1] > prev[1]
          || (rank[1] === prev[1] && rank[2] > prev[2])));
        if (better) winners[prop] = { value: norm(raw), rank };
      }
    }
    return Object.fromEntries(Object.entries(winners).map(([k, v]) => [k, v.value]));
  };
  const elements = (prefix) => {
    const root = { tag: 'div', classes: ['editor-shell', `${prefix}-editor`], ancestors: [] };
    const main = { tag: 'div', classes: ['editor-shell__main'], ancestors: [root] };
    const canvas = { tag: 'div', classes: ['editor-shell__canvas', `${prefix}-canvas`], ancestors: [main, root] };
    const hex = { tag: 'canvas', classes: ['editor-shell__hex'], ancestors: [canvas, main, root] };
    const flow = { tag: 'div', classes: ['react-flow'], ancestors: [canvas, main, root] };
    const renderer = { tag: 'div', classes: ['react-flow__renderer'], ancestors: [flow, canvas, main, root] };
    const error = { tag: 'div', classes: ['editor-shell__error', `${prefix}-error`], ancestors: [] };
    const message = { tag: 'p', classes: ['editor-shell__error-message'], ancestors: [error] };
    const btn = { tag: 'button', classes: ['editor-shell__error-btn'], ancestors: [error] };
    return { root, main, canvas, hex, flow, renderer, error, message, btn, 'btn:hover': btn };
  };
  const LAYER = { position: 'absolute', inset: '0', width: '100%', height: '100%', 'pointer-events': 'none', 'z-index': '0' };
  const FLEX_CENTER = { display: 'flex', 'flex-direction': 'column', 'align-items': 'center', 'justify-content': 'center', 'text-align': 'center' };
  const OLD = {
    estrai: {
      root: { width: '100%', height: '100vh', display: 'flex', 'flex-direction': 'column', background: 'var(--plen-bg)',
        color: 'var(--plen-text)', 'font-family': 'var(--plen-font-body)', 'font-size': '13px' },
      main: { flex: '1', display: 'flex', overflow: 'hidden' },
      canvas: { flex: '1', position: 'relative', overflow: 'hidden', background: 'var(--plen-bg)' },
      hex: LAYER,
      flow: { position: 'absolute', inset: '0', 'z-index': '1', background: 'transparent !important' },
      error: { ...FLEX_CENTER, height: '100%', color: '#F87171', padding: '20px', gap: '12px' },
      message: { 'font-size': '15px' },
      btn: { padding: '8px 20px', background: '#F87171', color: '#fff', border: 'none', 'border-radius': '8px',
        cursor: 'pointer', 'font-size': '13px', 'font-weight': '600' },
    },
    manipola: {
      root: { width: '100%', height: '100vh', display: 'flex', 'flex-direction': 'column', background: 'var(--plen-bg)',
        color: 'var(--plen-text)', 'font-family': 'var(--plen-font-body)' },
      main: { flex: '1', display: 'flex', overflow: 'hidden', 'min-height': '0' },
      canvas: { flex: '1', position: 'relative', height: '100%', background: 'var(--plen-bg)' },
      hex: LAYER,
      error: { ...FLEX_CENTER, height: '100%', gap: '14px', color: 'var(--plen-err)', padding: '24px' },
      message: { 'font-size': '13.5px' },
      btn: { padding: '7px 16px', 'border-radius': '6px', border: '1px solid var(--plen-border-hi)', background: 'transparent',
        color: 'var(--plen-text-sub)', 'font-size': '12px', 'font-weight': '600', cursor: 'pointer', transition: 'all 0.12s' },
      'btn:hover': { 'border-color': 'var(--plen-manipola)', color: 'var(--plen-manipola)' },
    },
    arricchisci: {
      root: { width: '100%', height: '100vh', display: 'flex', 'flex-direction': 'column', background: 'var(--plen-bg)',
        overflow: 'hidden' },
      main: { flex: '1', display: 'flex', overflow: 'hidden', 'min-height': '0' },
      canvas: { flex: '1', position: 'relative', height: '100%', overflow: 'hidden', background: 'var(--plen-bg)' },
      hex: LAYER,
      renderer: { background: 'transparent !important' },
      error: { ...FLEX_CENTER, height: '100vh', gap: '14px', background: 'var(--plen-bg)', color: 'var(--plen-err)',
        padding: '24px' },
    },
  };
  // Proprietà in più rispetto a 12e33b9d, con il perché (nessun cambio
  // visibile, o stile dato a ciò che in 12e33b9d non aveva regole).
  const ADDED = {
    root: ['color', 'font-family'], // Arricchisci: uguali a quelli ereditati dal body
    main: ['min-height'], // Estrai: con overflow hidden min-height auto vale già 0
    canvas: ['min-width', 'height'], // overflow hidden / stretch: stesso riempimento del main
    flow: ['position', 'inset', 'z-index'], // Manipola/Arricchisci: il grafo (position relative, 100%) riempiva già il canvas sopra gli esagoni
    renderer: ['background'], // Estrai/Manipola: grafo già trasparente, sotto ci sono gli esagoni
    error: ['background'], // Estrai/Manipola: transparent come prima (nessuno sfondo)
    message: ['margin', 'font-size'], // margin 0 = reset globale (* { margin: 0 }); Arricchisci: 15px (prima nessuna regola)
    btn: ['font-family', 'padding', 'background', 'color', 'border', 'border-radius', 'cursor', 'font-size', 'font-weight'], // Arricchisci: prima pulsante del browser senza regole; font del guscio
    'btn:hover': ['filter'], // passaggio del mouse del guscio (Estrai, Arricchisci)
  };
  const EDITOR_FILES = { estrai: 'styles/EstraiEditor.css', manipola: 'styles/ManipolaEditor.css', arricchisci: 'styles/ArricchisciEditor.css' };
  const cssRules = (prefix) => [
    ...rules(read('components/editor/EditorShell.css'), 0),
    ...rules(read(EDITOR_FILES[prefix]), 1),
  ];

  it.each(['estrai', 'manipola', 'arricchisci'])('%s: ogni proprietà di 12e33b9d ha lo stesso valore', (prefix) => {
    const all = cssRules(prefix);
    const els = elements(prefix);
    for (const [name, want] of Object.entries(OLD[prefix])) {
      const got = effective(all, els[name], name.endsWith(':hover') ? 'hover' : null);
      const picked = Object.fromEntries(Object.keys(want).map(k => [k, got[k]]));
      expect({ prefix, name, ...picked }).toEqual({ prefix, name, ...want });
    }
  });

  it.each(['estrai', 'manipola', 'arricchisci'])('%s: nessuna proprietà nuova oltre a quelle motivate', (prefix) => {
    const all = cssRules(prefix);
    const els = elements(prefix);
    for (const name of Object.keys(els)) {
      const state = name.endsWith(':hover') ? 'hover' : null;
      const got = effective(all, els[name], state);
      const base = state ? effective(all, els[name], null) : {};
      const old = { ...(state ? OLD[prefix][name.replace(':hover', '')] : {}), ...(OLD[prefix][name] || {}) };
      const extra = Object.keys(got).filter(k => !(k in old) && !(state && got[k] === base[k])
        && !(ADDED[name] || []).includes(k));
      expect({ prefix, name, extra }).toEqual({ prefix, name, extra: [] });
    }
  });

  it('Estrai: sidebar a 220px sotto 1024px (12e33b9d)', () => {
    const css = read('styles/EstraiEditor.css').replace(/\s+/g, ' ');
    expect(css).toContain('@media (max-width: 1024px) { .estrai-sidebar { width: 220px; } }');
  });
});
