import { describe, it, expect, beforeEach } from 'vitest';
import useCanvasStore from '../../src/store/useCanvasStore';
import { uniqueDataEdges } from '../../src/utils/runtimeBindingEdges';

/**
 * Collegamenti doppi (segnalazione dell'utente, 04/11/2026): nodi con due
 * frecce pur avendo un solo ingresso. Canvas salvati dalle versioni
 * precedenti con lo stesso collegamento due volte, una con l'aggancio
 * "input-0" e una senza aggancio. Regola unica dello store: tra due nodi un
 * collegamento di dati esiste una volta per aggancio.
 */
const n = (id, data = {}) => ({ id, type: 'transform', position: { x: 0, y: 0 }, data });
const e = (id, source, target, targetHandle = null, extra = {}) => ({ id, source, target, sourceHandle: null, targetHandle, ...extra });
const LEGACY = [
  e('reactflow__edge-s-tinput-0', 's', 't', 'input-0'),
  e('reactflow__edge-s-t', 's', 't', null),
];

beforeEach(() => {
  const st = useCanvasStore.getState();
  st.reset();
  st.setReadOnly(false);
  st.init('p1', 'manipola');
});

describe('collegamenti di dati unici', () => {
  it('canvas vecchio: il doppione senza aggancio sparisce al caricamento e la correzione si salva', () => {
    useCanvasStore.getState().loadGraph([n('s'), n('t')], LEGACY);
    const st = useCanvasStore.getState();
    expect(st.edges.map(x => x.id)).toEqual(['reactflow__edge-s-tinput-0']);
    expect(st.pendingSync).toBe(true);
    expect(st.isDirty).toBe(false);
  });

  it('canvas pulito: nessuna correzione', () => {
    const edges = [LEGACY[0]];
    useCanvasStore.getState().loadGraph([n('s'), n('t')], edges);
    expect(useCanvasStore.getState().edges).toBe(edges);
    expect(useCanvasStore.getState().pendingSync).toBe(false);
  });

  it('agganci diversi restano (Join di una tabella con se stessa); i collegamenti dei parametri non si toccano', () => {
    const edges = [
      e('a', 's', 't', 'input-0'), e('b', 's', 't', 'input-1'),
      e('p1', 'in', 't', 'parameter:x', { data: { edge_kind: 'parameter_binding' } }),
    ];
    expect(uniqueDataEdges(edges)).toBe(edges);
  });

  it('ogni percorso che aggiunge archi passa dalla regola', () => {
    const st = () => useCanvasStore.getState();
    st().loadGraph([n('s'), n('t')], [LEGACY[0]]);
    st().addEdges(LEGACY[1]);
    expect(st().edges.length).toBe(1);
    st().editEdges(edges => edges.concat({ ...LEGACY[0], id: 'copia' }));
    expect(st().edges.length).toBe(1);
    st().applyDerivedEdges(edges => edges.concat(LEGACY[1]));
    expect(st().edges.length).toBe(1);
    st().onEdgesChange([{ type: 'add', item: LEGACY[1] }]);
    expect(st().edges.length).toBe(1);
  });
});
