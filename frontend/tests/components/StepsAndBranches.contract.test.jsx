import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import useCanvasStore from '../../src/store/useCanvasStore';
import StepsView from '../../src/components/canvas/StepsView';
import BranchSection from '../../src/components/canvas/BranchSection';
import { CanvasGraphContext } from '../../src/components/canvas/CanvasGraphContext';
import { analyzeCanvas, structureKey } from '../../src/utils/canvasGraph';

/** Contract Fase 4 M6: vista Passi e azioni sui rami nel pannello. */
const n = (id, type = 'transform', data = {}) => ({ id, type, position: { x: 0, y: 0 }, data });
const e = (source, target) => ({ id: `${source}-${target}`, source, target });

// Come EditorShell: l'analisi condivisa nel contesto.
function withGraph(ui) {
  const { nodes, edges } = useCanvasStore.getState();
  const graph = { key: structureKey(nodes, edges), ...analyzeCanvas(nodes, edges) };
  return <CanvasGraphContext.Provider value={graph}>{ui}</CanvasGraphContext.Provider>;
}

beforeEach(() => {
  useCanvasStore.getState().reset();
  useCanvasStore.getState().setReadOnly(false);
  useCanvasStore.getState().loadGraph([
    n('s1', 'project_source', { project_name: 'Canoni' }), n('f', 'transform', { transform_op: 'filter', config: {}, title: 'Escludi periodi' }),
    n('out', 'result', { fileName: 'out' }),
    n('s2', 'project_source', { project_name: 'Contratti' }), n('g', 'transform', { transform_op: 'filter', config: {}, title: 'Solo attivi' }),
  ], [e('s1', 'f'), e('f', 'out'), e('s2', 'g')]);
});

describe('StepsView', () => {
  it('one lane per source, flags the dangling one, exclusive selection, adds before the Output', () => {
    useCanvasStore.setState(s => ({ edges: s.edges.map(x => (x.id === 's2-g' ? { ...x, selected: true } : x)) }));
    const onAddAfter = vi.fn();
    render(withGraph(<StepsView onAddAfter={onAddAfter} />));
    expect(screen.getByText(/Ramo da Canoni/)).toBeTruthy();
    expect(screen.getByText(/non arriva all'Output/)).toBeTruthy(); // ramo Contratti
    fireEvent.click(screen.getByText('Solo attivi'));
    const st = useCanvasStore.getState();
    expect(st.nodes.filter(x => x.selected).map(x => x.id)).toEqual(['g']);
    expect(st.edges.some(x => x.selected)).toBe(false); // niente edge selezionati: Canc non li cancella
    fireEvent.click(screen.getAllByText(/Aggiungi passo/)[0]);
    expect(onAddAfter).toHaveBeenCalledWith('f'); // non dopo l'Output
  });
});

describe('BranchSection', () => {
  const nodeById = (id) => useCanvasStore.getState().nodes.find(x => x.id === id);

  it('offers "Collega" on the last step of a dangling branch; targets show step and branch', () => {
    const connectBranch = vi.fn();
    render(withGraph(<BranchSection node={nodeById('g')} branchActions={{ connectBranch, connectModes: [['join', 'Unisci con (Join)'], ['concat', 'Accoda a (Concatena)']] }} />));
    const select = screen.getByLabelText('Passo a cui collegare');
    expect([...select.options].map(o => o.textContent)).toEqual(expect.arrayContaining(['Escludi periodi · Canoni', 'Canoni · Canoni']));
    fireEvent.change(select, { target: { value: 'f' } });
    fireEvent.change(screen.getByLabelText('Come collegare'), { target: { value: 'concat' } });
    fireEvent.click(screen.getByRole('button', { name: 'Collega' }));
    expect(connectBranch).toHaveBeenCalledWith('g', 'f', 'concat');
  });

  it('"Collega" never offers a node upstream of the branch (it would create a cycle)', () => {
    useCanvasStore.getState().loadGraph([
      n('s', 'project_source', { project_name: 'S' }), n('a', 'transform', { title: 'A' }), n('o', 'result'),
      n('b', 'transform', { title: 'B' }),
    ], [e('s', 'a'), e('a', 'o'), e('a', 'b')]);
    render(withGraph(<BranchSection node={nodeById('b')} branchActions={{ connectBranch: vi.fn() }} />));
    expect(screen.queryByLabelText('Passo a cui collegare')).toBeNull(); // A e S sono a monte di B
    expect(screen.getByText(/tutti a monte di questo ramo/)).toBeTruthy();
  });

  it('a middle step of a dangling branch leads to its last step', () => {
    render(withGraph(<BranchSection node={nodeById('s2')} branchActions={{ connectBranch: vi.fn() }} />));
    fireEvent.click(screen.getByRole('button', { name: /Vai all'ultimo passo/ }));
    expect(nodeById('g').selected).toBe(true);
  });

  it('without an editor action it explains how (Arricchisci)', () => {
    render(withGraph(<BranchSection node={nodeById('g')} branchActions={{}} />));
    expect(screen.getByText(/trascinando dalla sua uscita/)).toBeTruthy();
  });

  it('"Duplica ramo" on a source with steps, onto any other source', () => {
    const duplicateBranch = vi.fn();
    render(withGraph(<BranchSection node={nodeById('s1')} branchActions={{ duplicateBranch }} />));
    expect(screen.getByText(/1 passo/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Sorgente su cui duplicare'), { target: { value: 's2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Duplica ramo' }));
    expect(duplicateBranch).toHaveBeenCalledWith('s1', 's2');
  });

  it('read-only: only the warning', () => {
    useCanvasStore.getState().setReadOnly(true);
    render(withGraph(<BranchSection node={nodeById('g')} branchActions={{ connectBranch: vi.fn() }} />));
    expect(screen.getByText(/non arriva all'Output/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Collega' })).toBeNull();
  });
});

describe('store: Collega and Duplica are single, complete actions', () => {
  const mk = (p) => ({ ...p, id: `${p.source}-${p.target}-${p.targetHandle || ''}` });

  it('insertBetween moves ALL outputs of the target (also a fork) and adds the branch edge; one undo', () => {
    const s = useCanvasStore.getState();
    s.loadGraph([n('t'), n('y'), n('z'), n('end')], [e('t', 'y'), { ...e('t', 'z'), targetHandle: 'input-1' }]);
    const done = s.insertBetween({ node: n('j'), targetId: 't', makeEdge: mk, extraEdges: [mk({ source: 'end', target: 'j', targetHandle: 'input-1' })] });
    expect(done).toBeTruthy();
    expect(useCanvasStore.getState().edges.map(x => `${x.source}>${x.target}:${x.targetHandle || ''}`).sort())
      .toEqual(['end>j:input-1', 'j>y:', 'j>z:input-1', 't>j:input-0']);
    useCanvasStore.getState().undo();
    expect(useCanvasStore.getState().nodes.map(x => x.id)).toEqual(['t', 'y', 'z', 'end']);
  });

  it('addSubgraph adds a block without overlaps and selects the given node', () => {
    const s = useCanvasStore.getState();
    s.loadGraph([n('a')], []);
    s.addSubgraph({ nodes: [n('c1'), { ...n('c2'), position: { x: 300, y: 0 } }], edges: [e('c1', 'c2')], selectId: 'c2' });
    const st = useCanvasStore.getState();
    expect(st.nodes.find(x => x.id === 'c1').position.y).toBeGreaterThan(0); // sceso sotto 'a', insieme a c2
    expect(st.nodes.find(x => x.id === 'c2').position.y).toBe(st.nodes.find(x => x.id === 'c1').position.y);
    expect(st.nodes.find(x => x.id === 'c2').selected).toBe(true);
    expect(st.edges.map(x => x.id)).toEqual(['c1-c2']);
  });

  it('both are no-ops in read-only mode', () => {
    const s = useCanvasStore.getState();
    s.setReadOnly(true);
    expect(s.insertBetween({ node: n('j'), targetId: 's1', makeEdge: mk })).toBeNull();
    expect(s.addSubgraph({ nodes: [n('c')] })).toBeNull();
  });
});
