/**
 * Contract del guscio del pannello laterale (Blocco A, regola 6) e dei
 * titoli (regola 7), per enumerazione.
 *
 * Per OGNI tipo di nodo di ogni mappa `nodeTypes` dei tre editor (Estrai,
 * Manipola, Arricchisci; ricavate dai file degli editor, come
 * compactCanvas.contract) NodeInspector monta il modulo completo e:
 *  - c'e' esattamente un controllo di chiusura e un solo "Elimina passo",
 *    che chiede conferma (dialog del progetto, ConfirmProvider) e poi elimina;
 *  - nel corpo nessun elemento con classe che termina in "__header" e
 *    nessun altro pulsante che elimini il nodo (× o "Elimina nodo");
 *  - il modulo non riceve `selected` forzato;
 *  - in sola lettura niente "Elimina passo";
 *  - fuoco e uscita dal campo titolo senza modifiche non scrivono e non
 *    rendono il canvas "non salvato".
 * Un tipo nuovo in un editor, con un componente non elencato qui, fa
 * fallire il test.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ConfirmProvider } from '../../src/context/ConfirmContext';
import { answerConfirm } from '../helpers/confirmDialog';

const { canvasStateRef, httpMode } = vi.hoisted(() => ({
  canvasStateRef: { current: { nodes: [], edges: [] } },
  // 'pending': nessuna risposta; 'reject': ogni richiesta fallisce (gli
  // elenchi mostrano «Riprova»).
  httpMode: { current: 'pending' },
}));

vi.mock('@xyflow/react', () => ({
  ...globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef, showRoot: false }),
  // Fuori dal canvas: NodeHandle non disegna nulla, come nel pannello vero.
  useNodeId: () => null,
}));
// Nessuna risposta del server (o un errore, con httpMode 'reject').
vi.mock('../../src/api/http', () => {
  const pending = () => (httpMode.current === 'reject'
    ? Promise.reject(Object.assign(new Error('Servizio non raggiungibile'), { response: { status: 503, data: { detail: 'Servizio non raggiungibile' } } }))
    : new Promise(() => {}));
  return {
    default: {
      get: pending, post: pending, put: pending, patch: pending, delete: pending,
      interceptors: { request: { use: () => {} }, response: { use: () => {} } },
      defaults: { headers: { common: {} } },
    },
  };
});
vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../src/components/modals/RunHistoryModal', () => ({ default: () => null }));
vi.mock('../../src/utils/transformRegistry', () => ({
  fetchTransformRegistry: vi.fn(() => new Promise(() => {})),
  getCachedTransformRegistry: () => ({
    categories: { base: { id: 'base', name: 'Base', icon: '📊' } },
    transforms: {
      filter: { name: 'Filtra righe', category: 'base', icon: '🔍', inputs: 'single', parameters: [] },
      join: { name: 'Join', category: 'base', icon: '🔗', inputs: 'multi', parameters: [] },
    },
  }),
  isMultiInputOp: (op) => op === 'join',
}));

import useCanvasStore from '../../src/store/useCanvasStore';
import NodeInspector from '../../src/components/canvas/NodeInspector';
import ResultInlinePreview from '../../src/components/nodes/result/ResultInlinePreview';
import { ResourceError } from '../../src/components/common/ListState';
import { READ_ONLY_SAFE, isReadOnlySafe } from '../../src/components/common/readOnlySafe';
import { CASES, COMPONENTS, editorMaps, mountInspector } from '../helpers/inspectorHarness';

const deleteSpy = vi.fn();
let seenProps;

function mount(c, { readOnly = false, ...opts } = {}) {
  // Il modulo e' avvolto per leggere le props che riceve dal guscio.
  return mountInspector(c, {
    readOnly,
    canvasStateRef,
    storePatch: { deleteNode: deleteSpy },
    wrap: (Full) => (props) => { seenProps = props; return <Full {...props} />; },
    ...opts,
  });
}

const bodyOf = (container) => container.querySelector('.node-inspector__body');

beforeEach(() => {
  deleteSpy.mockReset();
  seenProps = null;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  httpMode.current = 'pending';
});

describe('Blocco A, regola 6: il guscio possiede intestazione, chiusura ed eliminazione', () => {
  it('le mappe degli editor sono state lette e ogni componente e\' elencato', () => {
    expect(CASES.length).toBeGreaterThan(20);
    const unknown = editorMaps.flatMap(({ file, pairs }) => pairs
      .filter(([, c]) => !COMPONENTS[c]).map(([t, c]) => `${file}: ${t} -> ${c}`));
    expect(unknown).toEqual([]);
  });

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: una chiusura, un solo "Elimina passo", nessuna intestazione propria', async (_, c) => {
    const { container } = mount(c);
    expect(screen.getAllByRole('button', { name: 'Chiudi pannello' })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Elimina passo' })).toHaveLength(1);

    const body = bodyOf(container);
    const headers = [...body.querySelectorAll('[class]')]
      .filter(el => [...el.classList].some(cls => cls.endsWith('__header')))
      .map(el => el.className);
    expect(headers).toEqual([]);

    // Nessun altro pulsante che elimini il nodo.
    const deleting = [...body.querySelectorAll('button')].filter(b => /Elimina nodo/i.test(
      `${b.getAttribute('title') || ''} ${b.getAttribute('aria-label') || ''}`,
    ));
    expect(deleting.map(b => b.outerHTML)).toEqual([]);
    for (const b of [...body.querySelectorAll('button')].filter(x => x.textContent.trim() === '×' && !x.disabled)) {
      await act(async () => { fireEvent.click(b); });
    }
    expect(deleteSpy).not.toHaveBeenCalled();

    // Niente `selected` forzato dentro il pannello.
    expect(seenProps).toBeTruthy();
    expect(seenProps.selected).toBeUndefined();
  });

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: "Elimina passo" chiede conferma e poi elimina', async (_, c) => {
    mount(c);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Elimina passo' })); });
    await answerConfirm(false);
    expect(deleteSpy).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Elimina passo' })); });
    await answerConfirm(true, { confirmText: 'Elimina' });
    expect(deleteSpy).toHaveBeenCalledWith('n1');
  });

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: in sola lettura niente "Elimina passo" e titolo bloccato', (_, c) => {
    mount(c, { readOnly: true });
    expect(screen.queryByRole('button', { name: 'Elimina passo' })).toBeNull();
    expect(screen.getByLabelText('Titolo del passo').disabled).toBe(true);
  });

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: fuoco e uscita dal titolo senza modifiche non scrivono', (_, c) => {
    mount(c);
    const before = useCanvasStore.getState();
    const field = screen.getByLabelText('Titolo del passo');
    expect(field.value).toBe('');
    expect(field.placeholder).not.toBe('');
    fireEvent.focus(field);
    fireEvent.blur(field);
    fireEvent.keyDown(field, { key: 'Enter' });
    const after = useCanvasStore.getState();
    expect(after.nodes[0].data.title).toBeUndefined();
    expect(after.isDirty).toBe(before.isDirty);
    expect(after.past.length).toBe(before.past.length);
  });
});

// Fase 5, T5+T6, correzione dopo la conferma. REGOLA della sola lettura: si
// spengono SOLO i controlli che modificano il canvas; le azioni di lettura,
// dichiarate in UN punto (`{...READ_ONLY_SAFE}`, common/readOnlySafe),
// restano attive. Per ogni pannello di ogni editor.
describe('Sola lettura: spenti i controlli di modifica, attive le azioni di lettura', () => {
  const CONTROLS = 'input, select, textarea, button';
  // Le sole azioni di lettura ammesse (etichetta, titolo o valore).
  const READ_ACTIONS = /Cronologia esecuzioni|Valida|Anteprima|Esegui|Ricarica anteprima|Apri anteprima completa|Riprova|Cerca colonna|^cache$|^recalculate$/;
  // Azioni di lettura che il viewer aveva a 8461197c, per tipo di nodo
  // (stessi dati minimi; sonda del giro di conferma su 8461197c).
  const VIEWER_8461197C = {
    'arricchisci · api_enrich': ['Cronologia esecuzioni'],
    'arricchisci · result': ['Cerca colonna', 'cache', 'recalculate', 'Valida', 'Cronologia esecuzioni'],
    'estrai · resultNode': ['Valida', 'Cronologia esecuzioni'],
    'manipola · multi_join': ['Cronologia esecuzioni'],
    'manipola · result': ['Cerca colonna', 'cache', 'recalculate', 'Valida', 'Cronologia esecuzioni'],
    'manipola · transform': ['Cronologia esecuzioni'],
  };
  const labelOf = (el) => [el.getAttribute('aria-label'), el.textContent, el.getAttribute('title'), el.value]
    .map(x => (x || '').trim().replace(/\s+/g, ' ')).filter(Boolean).join(' | ');
  const named = (el) => {
    const text = labelOf(el);
    const m = text.match(READ_ACTIONS);
    return m ? m[0] : text;
  };
  const editsActive = (controls) => controls
    .filter(el => !isReadOnlySafe(el) && !el.matches(':disabled')).map(el => el.outerHTML.slice(0, 120));
  let total = 0;

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: controlli di modifica spenti, azioni di lettura attive', (name, c) => {
    const { container } = mount(c, { readOnly: true });
    const form = container.querySelector('.node-inspector__form');
    expect(form).toBeTruthy();
    expect(form.dataset.readonly).toBe('true');
    const controls = [...form.querySelectorAll(CONTROLS)];
    total += controls.length;
    expect(editsActive(controls)).toEqual([]);
    const safe = controls.filter(isReadOnlySafe);
    expect(safe.map(labelOf).filter(l => !READ_ACTIONS.test(l))).toEqual([]);
    const activeReads = safe.filter(el => !el.disabled).map(named);
    expect(activeReads).toEqual(expect.arrayContaining(VIEWER_8461197C[name] || []));
  });

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: modificabile, il pannello non spegne nulla', (_, c) => {
    const { container } = mount(c);
    expect(container.querySelector('.node-inspector__form').dataset.readonly).toBeUndefined();
  });

  it('la verifica ha visto dei controlli (i pannelli si sono montati)', () => {
    expect(total).toBeGreaterThan(CASES.length);
  });

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: con il server in errore i «Riprova» restano attivi', async (_, c) => {
    globalThis.__frontendConsolePolicy.allowError = true;
    httpMode.current = 'reject';
    const { container } = mount(c, { readOnly: true });
    await act(async () => { for (let i = 0; i < 5; i += 1) await new Promise(r => setTimeout(r, 0)); });
    const controls = [...container.querySelector('.node-inspector__form').querySelectorAll(CONTROLS)];
    expect(editsActive(controls)).toEqual([]);
    const retries = controls.filter(el => /Riprova/.test(labelOf(el)));
    expect(retries.filter(el => el.disabled).map(el => el.outerHTML.slice(0, 120))).toEqual([]);
    retriesSeen += retries.length;
  });
  let retriesSeen = 0;
  it('la verifica in errore ha visto dei «Riprova»', () => {
    expect(retriesSeen).toBeGreaterThan(0);
  });

  it('Output: Valida, Anteprima, Esegui e Cronologia attivi per il viewer', () => {
    const c = CASES.find(x => x.mode === 'manipola' && x.type === 'result');
    mount(c, { readOnly: true, storePatch: { deleteNode: deleteSpy, editorActionsRef: { current: { onPreview: vi.fn(), onExecute: vi.fn() } } } });
    for (const name of [/Valida/, /Anteprima/, /Esegui/, /Cronologia esecuzioni/]) {
      const button = screen.getAllByRole('button').find(b => name.test(b.textContent));
      expect(button, String(name)).toBeTruthy();
      expect(button.disabled).toBe(false);
    }
  });

  const mountPanel = (Panel) => {
    const store = useCanvasStore.getState();
    store.reset(); store.init('p1', 'manipola'); store.setReadOnly(true);
    store.loadGraph([{ id: 'n1', type: 'x', position: { x: 0, y: 0 }, selected: true, data: {} }], []);
    return render(<ConfirmProvider><NodeInspector fullNodeTypes={{ x: Panel }} /></ConfirmProvider>);
  };

  it('anteprima del risultato ed elenchi in errore: Ricarica, Espandi e Riprova attivi; i controlli di modifica no', () => {
    mountPanel(() => (
      <div>
        <ResultInlinePreview state={{ status: 'success', data: { columns: [{ name: 'a' }], rows: [{ a: 1 }] } }} onRefresh={() => {}} onExpand={() => {}} />
        <ResourceError message="errore" onRetry={() => {}} />
        <input aria-label="campo" />
        <button type="button">Aggiungi</button>
        <button type="button" {...READ_ONLY_SAFE}>Valida</button>
      </div>
    ));
    expect(screen.getByTitle('Ricarica anteprima').disabled).toBe(false);
    expect(screen.getByTitle('Apri anteprima completa').disabled).toBe(false);
    expect(screen.getByRole('button', { name: 'Riprova' }).disabled).toBe(false);
    expect(screen.getByRole('button', { name: 'Valida' }).disabled).toBe(false);
    expect(screen.getByLabelText('campo').disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Aggiungi' }).disabled).toBe(true);
  });

  it('i controlli montati dopo (stato che cambia) sono spenti anche loro', async () => {
    const Later = () => {
      const [shown, setShown] = React.useState(false);
      React.useEffect(() => { setShown(true); }, []);
      return <div>{shown && <input aria-label="dopo" />}</div>;
    };
    mountPanel(Later);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByLabelText('dopo').disabled).toBe(true);
  });

  it('statico: ogni «Riprova» e ogni «Cronologia esecuzioni» dei pannelli e\' un\'azione di lettura dichiarata', () => {
    const src = path.resolve(import.meta.dirname, '../../src/components');
    const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => (
      e.isDirectory() ? walk(path.join(dir, e.name)) : (/\.jsx$/.test(e.name) ? [path.join(dir, e.name)] : [])));
    const files = ['nodes', 'panels', 'transforms', 'common'].flatMap(d => walk(path.join(src, d)));
    const offenders = [];
    let seen = 0;
    for (const file of files) {
      const text = fs.readFileSync(file, 'utf-8');
      for (const m of text.matchAll(/<button\b([^>]*)>([\s\S]{0,200}?)<\/button>/g)) {
        const isRead = /list-state__retry|colstatus__retry|setIsRunHistoryOpen\(true\)|__history-btn/.test(m[1]) || /^\s*Riprova\s*$/.test(m[2]);
        if (!isRead) continue;
        seen += 1;
        if (!m[1].includes('{...READ_ONLY_SAFE}')) offenders.push(`${path.basename(file)}: ${m[0].slice(0, 90)}`);
      }
    }
    expect(seen).toBeGreaterThan(10);
    expect(offenders).toEqual([]);
  });

  it('il form dell\'arco (join) segue la stessa regola', () => {
    const store = useCanvasStore.getState();
    store.reset();
    store.init('p1', 'estrai');
    store.setReadOnly(true);
    store.loadGraph([
      { id: 'a', type: 'tableNode', position: { x: 0, y: 0 }, data: {} },
      { id: 'b', type: 'tableNode', position: { x: 0, y: 0 }, data: {} },
    ], [{ id: 'e1', source: 'a', target: 'b', selected: true, data: {} }]);
    const edgeInspector = {
      kind: 'join', icon: '🔗', matches: () => true,
      entry: () => ({ title: 'Join', summary: '' }),
      Panel: () => <div><input aria-label="campo" /><select aria-label="scelta"><option>x</option></select><button type="button">Azione</button></div>,
    };
    const { container } = render(<ConfirmProvider><NodeInspector fullNodeTypes={{}} edgeInspector={edgeInspector} /></ConfirmProvider>);
    const form = container.querySelector('.node-inspector__form');
    expect(form).toBeTruthy();
    expect([...form.querySelectorAll(CONTROLS)].every(el => el.matches(':disabled'))).toBe(true);
    // La navigazione del pannello resta usabile.
    expect(screen.getByRole('button', { name: 'Chiudi pannello' }).disabled).toBe(false);
  });
});
