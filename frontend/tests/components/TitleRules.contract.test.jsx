/**
 * Contract dei titoli (Blocco A, regola 7): `data.title` contiene solo
 * testo dell'utente; il titolo derivato e' il segnaposto e segue da solo le
 * azioni che cambiano tipo, operazione o ramo.
 *
 * Per enumerazione:
 *  - ogni azione che cambia operazione o crea un passo (conversione in Mappa
 *    valori, cambio operazione, unione di piu' Filtra in uno, Collega,
 *    Duplica ramo) mostra il titolo derivato NUOVO, nel pannello e sul nodo;
 *  - un titolo congelato dalle versioni precedenti (uguale a un titolo
 *    generato) non nasconde il derivato;
 *  - campo del pannello e doppio clic sul nodo: fuoco e uscita senza
 *    modifiche non scrivono; svuotare o riscrivere il derivato torna al
 *    derivato; il campo segue undo e conversioni (controllato).
 */
import React from 'react';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { canvasStateRef } = vi.hoisted(() => ({ canvasStateRef: { current: { nodes: [], edges: [] } } }));

vi.mock('@xyflow/react', () => ({
  ...globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef, showRoot: false }),
  Position: { Left: 'left', Right: 'right', Top: 'top' },
}));
vi.mock('../../src/utils/transformRegistry', () => ({
  fetchTransformRegistry: vi.fn(() => new Promise(() => {})),
  getCachedTransformRegistry: () => ({
    transforms: {
      filter: { name: 'Filtra righe', icon: '🔍' },
      conditional: { name: 'Condizionale', icon: '🔀' },
      lookup: { name: 'Mappa valori', icon: '🗺️' },
      join: { name: 'Join', icon: '🔗' },
      concat: { name: 'Concatena', icon: '➕' },
    },
  }),
  isMultiInputOp: (op) => op === 'join' || op === 'concat',
}));

import useCanvasStore from '../../src/store/useCanvasStore';
import NodeInspector from '../../src/components/canvas/NodeInspector';
import CompactNode from '../../src/components/canvas/CompactNode';
import { derivedTitle, nodeTitle, titlePatch, userTitle } from '../../src/components/canvas/nodeDescriptors';
import { cloneStepData, connectStepData } from '../../src/utils/branchNodes';

const node = (data, extra = {}) => ({ id: 'n1', type: 'transform', position: { x: 0, y: 0 }, selected: true, data, ...extra });

function load(nodes) {
  const s = useCanvasStore.getState();
  s.reset();
  s.init('p1', 'manipola');
  s.setReadOnly(false);
  s.loadGraph(nodes, []);
  canvasStateRef.current = useCanvasStore.getState();
}

const current = () => useCanvasStore.getState().nodes.find(n => n.id === 'n1');
const field = () => screen.getByLabelText('Titolo del passo');

function renderBoth() {
  const n = current();
  return render(
    <>
      <NodeInspector fullNodeTypes={{}} />
      <CompactNode id={n.id} type={n.type} data={n.data} />
    </>,
  );
}

beforeEach(() => load([node({ transform_op: 'filter', config: {} })]));
afterEach(cleanup);

// Azioni che cambiano operazione, tipo o ramo: [nome, dati iniziali, azione, titolo atteso].
const ACTIONS = [
  ['conversione in Mappa valori', { transform_op: 'conditional', config: {} },
    () => useCanvasStore.getState().updateNodeData('n1', { transform_op: 'lookup', config: { column: 'a' } }), 'Mappa valori'],
  ['cambio operazione', { transform_op: 'filter', config: {} },
    () => useCanvasStore.getState().updateNodeData('n1', { transform_op: 'join', config: {} }), 'Join'],
  ['unione di piu\' Filtra in uno', { transform_op: 'filter', config: { column: 'a', operator: '==', value: 1 } },
    () => useCanvasStore.getState().patchNodeConfig('n1', { conditions: [{ column: 'a' }, { column: 'b' }] }), 'Filtra righe'],
  ['Collega (Join)', connectStepData('join', {}, 'Canoni'), null, 'Join · Canoni'],
  ['Collega, poi cambio in Concatena', connectStepData('join', {}, 'Canoni'),
    () => useCanvasStore.getState().updateNodeData('n1', { transform_op: 'concat', config: {} }), 'Concatena · Canoni'],
  ['Duplica ramo', cloneStepData({ id: 'x', type: 'transform', data: { transform_op: 'filter', config: {}, title: 'Filtra righe · Canoni' } }, 'Contratti'),
    null, 'Filtra righe · Contratti'],
  // Titolo congelato = un nome VERO del catalogo (tabella statica utils/operationNames, T6 R9-U N7).
  ['titolo congelato e cambio operazione', { transform_op: 'filter', config: {}, title: 'Filtra' },
    () => useCanvasStore.getState().updateNodeData('n1', { transform_op: 'lookup', config: {} }), 'Mappa valori'],
];

describe('Blocco A, regola 7: il titolo derivato segue le azioni', () => {
  it.each(ACTIONS)('%s', (_, data, action, expected) => {
    load([node(data)]);
    const { rerender } = renderBoth();
    if (action) act(() => action());
    const n = current();
    rerender(
      <>
        <NodeInspector fullNodeTypes={{}} />
        <CompactNode id={n.id} type={n.type} data={n.data} />
      </>,
    );
    expect(n.data.title === undefined || userTitle(n.type, n.data) === '').toBe(true);
    expect(nodeTitle(n.type, n.data)).toBe(expected);
    expect(field().value).toBe('');
    expect(field().placeholder).toBe(expected);
    expect(screen.getByTestId('compact-node').querySelector('.cnode__title').textContent).toBe(expected);
  });

  it('Collega e Duplica non scrivono data.title', () => {
    expect(connectStepData('concat', {}, 'Ramo').title).toBeUndefined();
    const clone = cloneStepData({ id: 'x', type: 'transform', data: { transform_op: 'filter', config: {}, title: 'Mio passo', columns: [{ name: 'a' }] } }, 'Altro');
    expect(clone.title).toBeUndefined();
    // Colonne derivate: mai nei dati del passo (Blocco A, regola 4).
    expect(clone).not.toHaveProperty('columns');
    expect(derivedTitle('transform', clone)).toBe('Filtra righe · Altro');
  });
});

describe('campo titolo del pannello (controllato)', () => {
  it('fuoco e uscita senza modifiche: nessuna scrittura, canvas pulito', () => {
    renderBoth();
    fireEvent.focus(field());
    fireEvent.blur(field());
    const s = useCanvasStore.getState();
    expect(s.isDirty).toBe(false);
    expect(s.past).toHaveLength(0);
    expect(current().data.title).toBeUndefined();
  });

  it('scrivere imposta il titolo; svuotarlo o riscrivere il derivato torna al derivato', () => {
    renderBoth();
    fireEvent.change(field(), { target: { value: '  Escludi periodi ' } });
    fireEvent.blur(field());
    expect(current().data.title).toBe('Escludi periodi');
    expect(useCanvasStore.getState().isDirty).toBe(true);

    fireEvent.change(field(), { target: { value: '' } });
    fireEvent.keyDown(field(), { key: 'Enter' });
    fireEvent.blur(field());
    expect(current().data.title).toBeUndefined();
    expect(field().value).toBe('');

    fireEvent.change(field(), { target: { value: 'Filtra righe' } });
    fireEvent.blur(field());
    expect(current().data.title).toBeUndefined();
  });

  it('segue undo e ripristino: mostra il valore corrente, non quello del montaggio', () => {
    renderBoth();
    fireEvent.change(field(), { target: { value: 'Escludi periodi' } });
    fireEvent.blur(field());
    expect(field().value).toBe('Escludi periodi');
    act(() => useCanvasStore.getState().undo());
    expect(field().value).toBe('');
    expect(field().placeholder).toBe('Filtra righe');
  });

  it('titlePatch: nessuna scrittura senza cambiamenti, anche con un titolo congelato', () => {
    const frozen = { transform_op: 'filter', title: 'Filtra righe' };
    expect(userTitle('transform', frozen)).toBe('');
    expect(titlePatch('transform', frozen, '')).toBeNull();
    expect(titlePatch('transform', frozen, 'Filtra righe')).toBeNull();
    expect(titlePatch('transform', { transform_op: 'filter', title: 'Mio' }, 'Mio ')).toBeNull();
    expect(titlePatch('transform', { transform_op: 'filter', title: 'Mio' }, '')).toEqual({ title: undefined, title_source: undefined });
    expect(titlePatch('transform', { transform_op: 'filter' }, 'Nuovo')).toEqual({ title: 'Nuovo', title_source: 'user' });
  });
});

describe('doppio clic sul nodo compatto (stessa regola)', () => {
  const editOnNode = () => {
    fireEvent.doubleClick(screen.getByTestId('compact-node').querySelector('.cnode__title'));
    return screen.getByTestId('compact-node').querySelector('.cnode__title-input');
  };

  it('apre il campo vuoto con il derivato come segnaposto; fuoco e uscita non scrivono', () => {
    renderBoth();
    const input = editOnNode();
    expect(input.value).toBe('');
    expect(input.placeholder).toBe('Filtra righe');
    fireEvent.blur(input);
    expect(useCanvasStore.getState().isDirty).toBe(false);
    expect(current().data.title).toBeUndefined();
  });

  it('Esc annulla senza scrivere; Invio scrive il testo dell\'utente', () => {
    renderBoth();
    let input = editOnNode();
    fireEvent.change(input, { target: { value: 'Annullato' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(current().data.title).toBeUndefined();

    input = editOnNode();
    fireEvent.change(input, { target: { value: 'Solo attivi' } });
    fireEvent.blur(input);
    expect(current().data.title).toBe('Solo attivi');
  });
});

// Giro di conferma F1/G: titolo dell'utente marcato, titoli vecchi riconosciuti, Esc.
describe('titoli: testo dell\'utente marcato, titoli vecchi solo per euristica', () => {
  it('un titolo scritto dall\'utente vale cosi\' com\'e\', anche se coincide con un nome generato', () => {
    const patch = titlePatch('transform', { transform_op: 'filter' }, 'Mappa valori');
    expect(patch).toEqual({ title: 'Mappa valori', title_source: 'user' });
    expect(nodeTitle('transform', { transform_op: 'filter', ...patch })).toBe('Mappa valori');
    expect(nodeTitle('tableNode', { table_name: 'clienti', title: 'Multi Join', title_source: 'user' })).toBe('Multi Join');
  });

  it('titoli vecchi senza marcatore: Collega, Duplica e nome della tabella non nascondono il derivato', () => {
    expect(userTitle('transform', { transform_op: 'join', title: 'Unisci Canoni' })).toBe('');
    expect(userTitle('transform', { transform_op: 'join', title: 'Accoda Canoni' })).toBe('');
    expect(userTitle('transform', { transform_op: 'filter', title: 'Filtra righe · Canoni' })).toBe('');
    expect(nodeTitle('tableNode', { table_name: 'clienti', alias: 'c', title: 'clienti' })).toBe('c');
    expect(userTitle('transform', { transform_op: 'filter', title: 'Solo attivi' })).toBe('Solo attivi');
  });

  it('svuotare il campo toglie anche il marcatore', () => {
    expect(titlePatch('transform', { transform_op: 'filter', title: 'X', title_source: 'user' }, ''))
      .toEqual({ title: undefined, title_source: undefined });
  });

  it('Esc nel pannello annulla senza scrivere', () => {
    load([node({ transform_op: 'filter', config: {} })]);
    render(<NodeInspector fullNodeTypes={{}} />);
    const spy = vi.spyOn(useCanvasStore.getState(), 'updateNodeData');
    fireEvent.change(field(), { target: { value: 'Da annullare' } });
    fireEvent.keyDown(field(), { key: 'Escape' });
    fireEvent.blur(field());
    expect(current().data.title).toBeUndefined();
    expect(useCanvasStore.getState().isDirty).toBe(false);
    spy.mockRestore();
  });
});
