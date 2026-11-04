/**
 * Contract del join di Estrai nel pannello laterale (Blocco A, raccolta b),
 * per enumerazione.
 *
 * Regola: ogni configurazione si modifica nel pannello a partire da una
 * SELEZIONE, nodo o arco. Il test copre:
 *  - OGNI modo di creare un join (trascinamento, palette, "Collega",
 *    tabella correlata ArcGIS): l'arco nasce "da completare" senza colonne
 *    scelte, resta selezionato (il pannello si apre su di lui), il canvas lo
 *    segnala e un solo annulla toglie tutto (b.1, b.2);
 *  - OGNI campo che nomina una colonna (JOIN_COLUMN_FIELDS, confrontata con
 *    il serializer): un valore non piu' presente mostra "(non trovata)" e
 *    blocca la scrittura finche' non si corregge (b.3);
 *  - cambio di tipo senza residui (b.4), nessuna scrittura senza modifiche
 *    (b.5), sola lettura (b.6), vista Passi (b.7), pannello della tabella e
 *    tasto destro (b.8), lettura sempre dallo stato corrente (b.9).
 */
import React from 'react';
import { render, screen, fireEvent, cleanup, act, renderHook, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import useCanvasStore from '../../src/store/useCanvasStore';
import NodeInspector from '../../src/components/canvas/NodeInspector';
import StepsView from '../../src/components/canvas/StepsView';
import JoinEdgePanel from '../../src/components/canvas/JoinEdgePanel';
import { CanvasGraphContext } from '../../src/components/canvas/CanvasGraphContext';
import { analyzeCanvas, structureKey } from '../../src/utils/canvasGraph';
import { serializeEdge } from '../../src/utils/canvasSerializer';
import { useEdgeHandlers } from '../../src/hooks/useEdgeHandlers';
import {
  JOIN_COLUMN_FIELDS, estraiJoinEdges, joinDataFromDraft, joinDraftFromData, joinStatus,
  paletteJoinEnds, presentJoinEdges, relationJoinEnds,
} from '../../src/utils/estraiJoins';

const edgeInspector = { ...estraiJoinEdges, Panel: JoinEdgePanel };
const FULL = { tableNode: () => <div>modulo tabella</div>, featureLayerNode: () => <div>modulo layer</div>, resultNode: () => null };

const table = (id, name, columns, extra = {}) => ({
  id, type: 'tableNode', position: { x: 0, y: 0 },
  data: { table_name: name, alias: null, columns: columns.map(([n, t = 'int']) => ({ name: n, type: t, visible: true })), ...extra },
});
const A = () => table('a', 'clienti', [['id'], ['geom', 'geometry(Point)'], ['nome', 'str']]);
const B = () => table('b', 'ordini', [['cliente_id'], ['stato', 'str'], ['area', 'geometry(Polygon)']]);
const OUT = () => ({ id: 'out', type: 'resultNode', position: { x: 600, y: 0 }, data: { fileName: 'out' } });
const outEdge = { id: 'b-out', source: 'b', target: 'out', data: { join_type: 'output' } };

const VALID = {
  join_type: 'left', is_spatial: false, source_column: 'id', target_column: 'cliente_id',
  join_conditions: [{ source_column: 'id', target_column: 'cliente_id', source_func: '', target_func: '' }],
  on_filters: [{ column: 'stato', operator: 'IS NULL', value: '', func: '' }],
  spatial_predicate: null, source_geom_column: null, target_geom_column: null, spatial_distance: null,
};
const VALID_SPATIAL = {
  ...joinDataFromDraft({ ...joinDraftFromData({}), mode: 'spatial', source_geom_column: 'geom', target_geom_column: 'area' }),
};
const joinEdge = (data, extra = {}) => ({ id: 'j1', source: 'a', target: 'b', data, ...extra });

function load(nodes, edges, { readOnly = false } = {}) {
  const s = useCanvasStore.getState();
  s.reset();
  s.init('p1', 'estrai');
  s.setReadOnly(readOnly);
  useCanvasStore.getState().loadGraph(nodes, edges);
}
const st = () => useCanvasStore.getState();
const edgeById = (id) => st().edges.find(e => e.id === id);
const withGraph = (ui) => {
  const { nodes, edges } = st();
  return <CanvasGraphContext.Provider value={{ key: structureKey(nodes, edges), ...analyzeCanvas(nodes, edges) }}>{ui}</CanvasGraphContext.Provider>;
};
const inspector = () => render(<NodeInspector fullNodeTypes={FULL} prefix="estrai" edgeInspector={edgeInspector} />);

beforeEach(() => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ── b.1 / b.2: ogni modo di creare un join ───────────────────────────────
const RELATION = { role: 'origin', cardinality: '1:N', name: 'clienti_ordini', key_field: 'id', key_field_in_relationship_table: 'cliente_id', related_url: 'u' };
const CREATE_WAYS = [
  ['trascinamento', (h) => { st().takeSnapshot(); h.onConnect({ source: 'a', sourceHandle: null, target: 'b', targetHandle: null }); }],
  ['palette (tabella dopo tabella)', (h) => {
    const node = table('n_new', 'righe', [['ordine_id']]);
    h.connectJoin({ node, ...paletteJoinEnds(node.id, 'b') });
  }],
  ['"Collega" del pannello', (h) => h.connectBranch('a', 'b')],
  ['tabella correlata ArcGIS', (h) => {
    const node = { id: 'fl_new', type: 'featureLayerNode', position: { x: 0, y: 350 }, data: { layer_name: 'ordini_rel', columns: [{ name: 'cliente_id', type: 'int' }] } };
    h.connectJoin({ node, ...relationJoinEnds('a', node.id, RELATION) });
  }],
];

describe('ogni modo di creare un join: nasce da completare, pannello aperto, un solo annulla', () => {
  it.each(CREATE_WAYS)('%s', (_, create) => {
    load([{ ...A(), selected: true }, B(), OUT()], [outEdge]);
    const before = { nodes: st().nodes.map(n => n.id), edges: st().edges.map(e => e.id) };
    const { result } = renderHook(() => useEdgeHandlers({ resultNodeType: 'resultNode' }));

    act(() => { create(result.current); });

    const added = st().edges.filter(e => !before.edges.includes(e.id));
    expect(added).toHaveLength(1);
    const edge = added[0];
    // Nessuna colonna scelta al posto dell'utente.
    expect(serializeEdge(edge)).toMatchObject({
      source_column: '', target_column: '', join_conditions: null, on_filters: null,
      is_spatial: false, source_geom_column: null, target_geom_column: null,
    });
    const byId = new Map(st().nodes.map(n => [n.id, n]));
    expect(joinStatus(edge.data, byId.get(edge.source), byId.get(edge.target))).toBe('incomplete');
    // Il canvas lo segnala.
    expect(presentJoinEdges(st().edges, st().nodes).find(e => e.id === edge.id).label).toMatch(/da completare/);
    // Selezionato da solo: il pannello si apre sul join.
    expect(st().nodes.filter(n => n.selected)).toEqual([]);
    expect(st().edges.filter(e => e.selected).map(e => e.id)).toEqual([edge.id]);
    inspector();
    expect(screen.getByRole('complementary', { name: 'Configurazione: Join' })).toBeTruthy();
    expect(screen.getByText(/Da completare:/)).toBeTruthy();
    cleanup();

    // Un solo annulla toglie nodo e arco.
    act(() => { st().undo(); });
    expect(st().nodes.map(n => n.id)).toEqual(before.nodes);
    expect(st().edges.map(e => e.id)).toEqual(before.edges);
  });

  it('la relazione ArcGIS resta un suggerimento: "Usa" la applica', () => {
    load([A(), B()], []);
    const { result } = renderHook(() => useEdgeHandlers());
    const node = { id: 'fl_new', type: 'featureLayerNode', position: { x: 0, y: 350 }, data: { layer_name: 'rel', columns: [{ name: 'cliente_id', type: 'int' }] } };
    let edge;
    act(() => { edge = result.current.connectJoin({ node, ...relationJoinEnds('a', node.id, RELATION) }); });
    inspector();
    fireEvent.click(screen.getByRole('button', { name: 'Usa' }));
    expect(edgeById(edge.id).data).toMatchObject({ source_column: 'id', target_column: 'cliente_id' });
  });

  it('trascinare di nuovo tra due tabelle gia\' unite apre il join esistente', () => {
    load([A(), B()], [joinEdge(VALID)]);
    const { result } = renderHook(() => useEdgeHandlers());
    act(() => { result.current.onConnect({ source: 'b', target: 'a' }); });
    expect(st().edges.map(e => e.id)).toEqual(['j1']);
    expect(edgeById('j1').selected).toBe(true);
  });

  it('in sola lettura nessun modo crea archi', () => {
    load([A(), B()], [], { readOnly: true });
    const { result } = renderHook(() => useEdgeHandlers());
    for (const [, create] of CREATE_WAYS) act(() => { create(result.current); });
    expect(st().edges).toEqual([]);
    expect(st().nodes.map(n => n.id)).toEqual(['a', 'b']);
  });
});

// ── b.3: ogni campo che nomina una colonna ───────────────────────────────
describe('ogni campo colonna: valore sparito = "(non trovata)" e scrittura bloccata', () => {
  it('la tabella dei campi copre ogni colonna che il serializer scrive', () => {
    const sample = serializeEdge(joinEdge(VALID));
    const spatial = serializeEdge(joinEdge(VALID_SPATIAL));
    const keys = new Set([
      ...Object.keys(sample).filter(k => /column/.test(k)),
      ...Object.keys(spatial).filter(k => /column/.test(k)),
      ...Object.keys(sample.join_conditions[0]).filter(k => /column/.test(k)).map(k => `condition.${k}`),
      ...Object.keys(sample.on_filters[0]).filter(k => /column/.test(k)).map(k => `on_filter.${k}`),
    ]);
    // source_column/target_column di primo livello ripetono la prima condizione.
    keys.delete('source_column');
    keys.delete('target_column');
    expect([...keys].sort()).toEqual(JOIN_COLUMN_FIELDS.map(f => f.id).sort());
  });

  const withMissing = (field) => {
    const draft = joinDraftFromData(field.mode === 'spatial' ? VALID_SPATIAL : VALID);
    if (field.list) draft[field.list][0] = { ...draft[field.list][0], [field.key]: 'sparita' };
    else draft[field.key] = 'sparita';
    return joinDataFromDraft(draft);
  };
  const validValue = { source: { column: 'nome', geom: 'geom' }, target: { column: 'stato', geom: 'area' } };

  it.each(JOIN_COLUMN_FIELDS.map(f => [f.id, f]))('%s', (_, field) => {
    const data = withMissing(field);
    load([A(), B()], [joinEdge(data, { selected: true })]);
    inspector();
    const label = field.list ? `${field.label} 1` : field.label;
    const select = screen.getByLabelText(label);
    expect(select.getAttribute('aria-invalid')).toBe('true');
    expect([...select.options].find(o => o.value === 'sparita').textContent).toMatch(/\(non trovata\)/);
    expect(screen.getByText(/«sparita» non trovata/)).toBeTruthy();
    expect(joinStatus(data, A(), B())).toBe('missing');

    // Un'altra modifica non si scrive finche' la colonna non e' corretta.
    fireEvent.change(screen.getByLabelText('Tipo join'), { target: { value: 'inner' } });
    expect(edgeById('j1').data).toEqual(data);
    expect(st().isDirty).toBe(false);

    // Corretta la colonna, si scrive tutto (anche il tipo scelto prima).
    fireEvent.change(select, { target: { value: validValue[field.side][field.kind] } });
    const written = edgeById('j1').data;
    expect(written.join_type).toBe('inner');
    expect(joinStatus(written, A(), B())).toBe('ok');
    expect(st().isDirty).toBe(true);
  });
});

// ── b.4 - b.9 ─────────────────────────────────────────────────────────────
describe('JoinEdgePanel', () => {
  it('b.5: aprire, guardare e rimettere lo stesso valore non scrive e non rende "non salvato"', () => {
    load([A(), B()], [joinEdge(VALID, { selected: true })]);
    inspector();
    const type = screen.getByLabelText('Tipo join');
    fireEvent.focus(type);
    fireEvent.change(type, { target: { value: 'left' } });
    fireEvent.blur(type);
    fireEvent.change(screen.getByLabelText('Colonna sorgente 1'), { target: { value: 'id' } });
    // Una riga vuota aggiunta non cambia la configurazione.
    fireEvent.click(screen.getByRole('button', { name: /Aggiungi condizione/ }));
    expect(st().isDirty).toBe(false);
    expect(st().past).toEqual([]);
    expect(edgeById('j1').data).toBe(VALID);
  });

  it('b.4: passando a spaziale e ritorno non restano campi dell\'altro tipo', () => {
    load([A(), B()], [joinEdge(VALID, { selected: true })]);
    inspector();
    fireEvent.click(screen.getByRole('button', { name: /Spaziale/ }));
    // Spaziale senza geometrie: non si scrive.
    expect(edgeById('j1').data).toBe(VALID);
    fireEvent.change(screen.getByLabelText('Geometria sorgente'), { target: { value: 'geom' } });
    fireEvent.change(screen.getByLabelText('Geometria target'), { target: { value: 'area' } });
    expect(serializeEdge(edgeById('j1'))).toMatchObject({
      is_spatial: true, source_column: '', target_column: '', join_conditions: null, on_filters: null,
      source_geom_column: 'geom', target_geom_column: 'area', spatial_distance: null,
    });
    fireEvent.click(screen.getByRole('button', { name: /Standard/ }));
    // La bozza standard e' ancora quella di prima: tornare scrive lo standard pulito.
    expect(serializeEdge(edgeById('j1'))).toMatchObject({
      is_spatial: false, source_column: 'id', target_column: 'cliente_id',
      spatial_predicate: null, source_geom_column: null, target_geom_column: null, spatial_distance: null,
    });
  });

  it('b.9: dopo annulla e ripeti il pannello mostra lo stato corrente dell\'arco', () => {
    load([A(), B()], [joinEdge(VALID, { selected: true })]);
    inspector();
    fireEvent.change(screen.getByLabelText('Tipo join'), { target: { value: 'inner' } });
    expect(edgeById('j1').data.join_type).toBe('inner');
    act(() => { st().undo(); });
    expect(screen.getByLabelText('Tipo join').value).toBe('left');
    act(() => { st().redo(); });
    expect(screen.getByLabelText('Tipo join').value).toBe('inner');
    // Nessuna istantanea: una modifica dopo l'annulla parte dallo stato corrente.
    act(() => { st().undo(); });
    fireEvent.change(screen.getByLabelText('Colonna target 1'), { target: { value: 'stato' } });
    expect(edgeById('j1').data).toMatchObject({ join_type: 'left', target_column: 'stato' });
  });

  it('b.6: in sola lettura tutto disabilitato, niente "Elimina join"', () => {
    load([A(), B()], [joinEdge(VALID, { selected: true })], { readOnly: true });
    const { container } = inspector();
    const body = container.querySelector('.node-inspector__body');
    const controls = [...body.querySelectorAll('select, input, button, textarea')];
    expect(controls.length).toBeGreaterThan(5);
    expect(controls.filter(c => !c.disabled).map(c => c.outerHTML)).toEqual([]);
    expect(screen.queryByRole('button', { name: 'Elimina join' })).toBeNull();
  });

  it('guscio: intestazione "Join", chiusura, "Elimina join" con conferma', async () => {
    load([A(), B()], [joinEdge(VALID, { selected: true })]);
    inspector();
    expect(screen.getByText(/Join/, { selector: '.node-inspector__kind' })).toBeTruthy();
    expect(screen.getByText('Join con clienti')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Chiudi pannello' })).toHaveLength(1);
    window.confirm.mockReturnValueOnce(false);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Elimina join' })); });
    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(edgeById('j1')).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Elimina join' })); });
    expect(edgeById('j1')).toBeUndefined();
    act(() => { st().undo(); });
    expect(edgeById('j1')).toBeTruthy();
  });

  it('la chiusura deseleziona l\'arco', () => {
    load([A(), B()], [joinEdge(VALID, { selected: true })]);
    inspector();
    fireEvent.click(screen.getByRole('button', { name: 'Chiudi pannello' }));
    expect(edgeById('j1').selected).toBe(false);
    expect(st().isDirty).toBe(false);
  });
});

describe('b.8: pannello della tabella e tasto destro', () => {
  it.each([['sorgente', 'a', 'Join con ordini'], ['target', 'b', 'Join con clienti']])('la tabella %s elenca il join e lo apre', (_, id, title) => {
    load([{ ...(id === 'a' ? A() : B()), selected: true }, id === 'a' ? B() : A()], [joinEdge(VALID)]);
    inspector();
    const list = screen.getByRole('region', { name: 'Join del passo' });
    fireEvent.click(within(list).getByRole('button', { name: new RegExp(title) }));
    expect(st().nodes.some(n => n.selected)).toBe(false);
    expect(edgeById('j1').selected).toBe(true);
    expect(screen.getByRole('complementary', { name: 'Configurazione: Join' })).toBeTruthy();
  });

  it('tasto destro = elimina l\'arco (annullabile), come in Manipola e Arricchisci; niente in sola lettura', () => {
    load([A(), B(), OUT()], [joinEdge(VALID), outEdge]);
    const { result } = renderHook(() => useEdgeHandlers());
    const event = { preventDefault: vi.fn() };
    act(() => { result.current.onEdgeContextMenu(event, edgeById('j1')); });
    expect(event.preventDefault).toHaveBeenCalled();
    expect(st().edges.map(e => e.id)).toEqual(['b-out']);
    act(() => { st().undo(); });
    expect(st().edges.map(e => e.id)).toEqual(['j1', 'b-out']);
    st().setReadOnly(true);
    act(() => { result.current.onEdgeContextMenu(event, edgeById('j1')); });
    expect(st().edges.map(e => e.id)).toEqual(['j1', 'b-out']);
  });

  it('gli archi verso Output e Carica non aprono il pannello del join', () => {
    load([A(), B(), OUT()], [{ ...outEdge, selected: true }]);
    const { container } = inspector();
    expect(container.innerHTML).toBe('');
  });
});

describe('b.7: la vista Passi mostra i join e li apre nel pannello', () => {
  it('ogni join compare una volta, sotto la tabella che unisce; un clic lo seleziona', () => {
    const C = table('c', 'prodotti', [['codice']]);
    load([A(), B(), C, OUT()], [joinEdge(VALID), { id: 'j2', source: 'c', target: 'b', data: joinDataFromDraft(joinDraftFromData({})) }, outEdge]);
    render(withGraph(<StepsView edgeInspector={edgeInspector} />));
    const entries = [...document.querySelectorAll('[data-edge-id]')].map(el => el.getAttribute('data-edge-id')).sort();
    expect(entries).toEqual(['j1', 'j2']);
    expect(screen.getByText('Join con prodotti').closest('button').textContent).toMatch(/da completare/);
    fireEvent.click(screen.getByText('Join con clienti'));
    expect(st().nodes.some(n => n.selected)).toBe(false);
    expect(st().edges.filter(e => e.selected).map(e => e.id)).toEqual(['j1']);
    expect(st().isDirty).toBe(false);
  });

  it('in sola lettura i join si aprono lo stesso (solo selezione)', () => {
    load([A(), B(), OUT()], [joinEdge(VALID), outEdge], { readOnly: true });
    render(withGraph(<StepsView edgeInspector={edgeInspector} />));
    fireEvent.click(screen.getByText('Join con clienti'));
    expect(edgeById('j1').selected).toBe(true);
  });
});
