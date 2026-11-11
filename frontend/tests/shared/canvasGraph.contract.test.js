import { describe, it, expect } from 'vitest';
import {
  analyzeCanvas, branchSteps, nodeRole, structureKey,
} from '../../src/utils/canvasGraph';

// Fase 5 T7: gli involucri reachingOutput/danglingNodeIds/stepLanes erano
// usati solo qui; l'app legge analyzeCanvas, e cosi' il test.
const reachingOutput = (ns, es) => analyzeCanvas(ns, es).reaching;
const danglingNodeIds = (ns, es) => analyzeCanvas(ns, es).dangling;
const stepLanes = (ns, es) => analyzeCanvas(ns, es).lanes.map(l => ({ ...l, steps: l.stepIds.map(id => ({ id })) }));

/** Contract Fase 4 M6: ruoli, Output raggiunto, corsie della vista Passi, ramo da duplicare. */
const n = (id, type = 'transform', data = {}) => ({ id, type, position: { x: 0, y: 0 }, data });
const e = (source, target, extra = {}) => ({ id: `${source}-${target}`, source, target, ...extra });

// s1 -> a -> b -> j ; s2 -> c -> j ; j -> out -> load ; s3 -> d (scollegato) ; file input -> f -> j
const nodes = [n('s1', 'project_source'), n('a'), n('b'), n('s2', 'project_source'), n('c'), n('j'),
  n('out', 'result'), n('s3', 'project_source'), n('d'), n('load', 'carica'), n('note', 'comment'),
  n('in', 'project_input', { input_kind: 'string' }), n('fi', 'project_input', { input_kind: 'file' }), n('f'),
  n('dsw', 'datasourceNode', { mode: 'carica' }), n('sv', 'staticValues')];
const edges = [e('s1', 'a'), e('a', 'b'), e('b', 'j'), e('s2', 'c'), e('c', 'j'), e('j', 'out'),
  e('s3', 'd'), e('out', 'load'), e('out', 'dsw'), e('fi', 'f'), e('f', 'j'),
  e('in', 'a', { targetHandle: 'parameter:value', data: { edge_kind: 'parameter_binding' } }),
  e('ghost', 'a')]; // edge verso un nodo cancellato: ignorato

describe('canvasGraph', () => {
  it('roles come from one table, including data-dependent ones', () => {
    expect(nodes.map(x => [x.id, nodeRole(x)])).toEqual([
      ['s1', 'source'], ['a', 'step'], ['b', 'step'], ['s2', 'source'], ['c', 'step'], ['j', 'step'],
      ['out', 'output'], ['s3', 'source'], ['d', 'step'], ['load', 'sink'], ['note', 'off'],
      ['in', 'off'], ['fi', 'source'], ['f', 'step'], ['dsw', 'sink'], ['sv', 'param'],
    ]);
  });

  it('reaching and dangling: only sources and steps can be dangling; no Output = nobody', () => {
    expect([...reachingOutput(nodes, edges)].sort()).toEqual(['a', 'b', 'c', 'f', 'fi', 'j', 'out', 's1', 's2']);
    expect([...danglingNodeIds(nodes, edges)].sort()).toEqual(['d', 's3']);
    expect(danglingNodeIds(nodes.filter(x => x.type !== 'result'), edges).size).toBe(0);
  });

  it('Estrai: a table joined "forward" from the root reaches the Output (JOIN in both directions)', () => {
    // Filtro «Utente che esegue» ignorato (11/11/2026): radice -> collegata,
    // la collegata non ha archi verso l'Output ma entra nella query.
    const tables = [n('val', 'tableNode'), n('cd', 'tableNode'), n('cd2', 'source'), n('lone', 'tableNode'),
      n('out', 'result')];
    const joins = [e('val', 'cd'), e('cd', 'cd2'), e('val', 'out')];
    expect([...reachingOutput(tables, joins)].sort()).toEqual(['cd', 'cd2', 'out', 'val']);
    expect([...danglingNodeIds(tables, joins)]).toEqual(['lone']);
    // Fuori da Estrai la direzione resta quella del flusso dei dati.
    const flow = [n('s', 'project_source'), n('a'), n('b'), n('out', 'result')];
    expect([...danglingNodeIds(flow, [e('s', 'a'), e('a', 'b'), e('a', 'out')])]).toEqual(['b']);
  });

  it('lanes in flow order; the Output lane continues into its loads; a fork opens new lanes', () => {
    const lanes = stepLanes(nodes, edges).map(l => [l.kind, l.steps.map(x => x.id), l.addAfterId, l.dangling]);
    expect(lanes).toEqual([
      ['source', ['s1', 'a', 'b'], 'b', false],
      ['source', ['s2', 'c'], 'c', false],
      ['source', ['s3', 'd'], 'd', true],
      ['source', ['fi', 'f'], 'f', false],
      ['source', ['sv'], 'sv', false],
      ['join', ['j', 'out'], 'j', false],
      ['fork', ['load'], null, false],
      ['fork', ['dsw'], null, false],
    ]);
  });

  it('every flow node appears in exactly one lane, on many random graphs (cycles included)', () => {
    let seed = 7;
    const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
    const types = ['project_source', 'transform', 'transform', 'transform', 'multi_join', 'result', 'carica', 'comment', 'staticValues'];
    for (let g = 0; g < 300; g += 1) {
      const ns = Array.from({ length: 2 + Math.floor(rnd() * 12) }, (_, i) => n(`n${i}`, types[Math.floor(rnd() * types.length)]));
      const es = [];
      for (let k = 0; k < Math.floor(rnd() * ns.length * 2); k += 1) {
        const a = ns[Math.floor(rnd() * ns.length)].id;
        const b = ns[Math.floor(rnd() * ns.length)].id;
        if (a !== b) es.push(e(a, b, { id: `e${k}` }));
      }
      const { lanes } = analyzeCanvas(ns, es);
      const seen = lanes.flatMap(l => l.stepIds);
      const flow = ns.filter(x => nodeRole(x) !== 'off').map(x => x.id).sort();
      expect([...seen].sort(), `graph ${g}`).toEqual(flow);
    }
  });

  it('the branch to duplicate = steps fed only by that source, also past a fork', () => {
    const ns = [n('s', 'project_source'), n('x'), n('y'), n('z'), n('t', 'project_source'), n('u'), n('o', 'result')];
    const es = [e('s', 'x'), e('x', 'y'), e('x', 'z'), e('t', 'u'), e('u', 'z'), e('y', 'o')];
    expect(branchSteps(ns, es, 's').map(x => x.id)).toEqual(['x', 'y']); // z ha anche t: non e' del ramo
  });

  it('the structure key ignores positions and data, follows links and roles', () => {
    const k = structureKey(nodes, edges);
    expect(structureKey(nodes.map(x => ({ ...x, position: { x: 9, y: 9 }, data: { ...x.data, title: 'z' } })), edges)).toBe(k);
    expect(structureKey(nodes, edges.slice(1))).not.toBe(k);
    expect(structureKey(nodes.map(x => (x.id === 'in' ? { ...x, data: { input_kind: 'file' } } : x)), edges)).not.toBe(k);
  });

  it('ancestors (never "Collega" targets) and dangling ends past a fork', () => {
    const ns = [n('s', 'project_source'), n('a'), n('b'), n('c'), n('o', 'result')];
    const es = [e('s', 'a'), e('a', 'o'), e('a', 'b'), e('b', 'c')];
    const g = analyzeCanvas(ns, es);
    expect([...g.ancestorsOf('c')].sort()).toEqual(['a', 'b', 's']);
    const fork = [n('s', 'project_source'), n('a'), n('b'), n('c'), n('o', 'result')];
    const g2 = analyzeCanvas(fork, [e('s', 'a'), e('a', 'b'), e('a', 'c')]);
    expect(g2.danglingEndsFrom('s').sort()).toEqual(['b', 'c']);
  });

  it('a parameter input (Valori statici) does not split a branch nor take it over, whatever the node order', () => {
    const base = [n('s', 'project_source'), n('sv', 'staticValues'), n('x', 'transform'), n('y'), n('o', 'result')];
    const es = [e('s', 'x'), e('sv', 'x'), e('x', 'y'), e('y', 'o')];
    for (const ns of [base, [base[1], base[0], ...base.slice(2)]]) {
      expect(branchSteps(ns, es, 's').map(x => x.id)).toEqual(['x', 'y']);
      const lanes = stepLanes(ns, es);
      expect(lanes.find(l => l.id === 's').steps.map(x => x.id)).toEqual(['s', 'x', 'y', 'o']);
      expect(lanes.find(l => l.id === 'sv').steps.map(x => x.id)).toEqual(['sv']);
    }
  });

  it('a dangling step feeding only a load is the end of its branch', () => {
    const ns = [n('s', 'project_source'), n('a'), n('k', 'carica'), n('o', 'result')];
    const g = analyzeCanvas(ns, [e('s', 'a'), e('a', 'k')]);
    expect(g.isEnd('a')).toBe(true);
    expect(g.danglingEndsFrom('s')).toEqual(['a']);
  });

  it('a repeated link between the same nodes (saved duplicate edges) is one input', () => {
    const ns = [n('s', 'project_source'), n('a'), n('b'), n('o', 'result')];
    const es = [e('s', 'a'), e('a', 'b', { id: 'd1' }), e('a', 'b', { id: 'd2' }), e('b', 'o')];
    expect(stepLanes(ns, es).map(l => l.steps.map(x => x.id))).toEqual([['s', 'a', 'b', 'o']]);
  });
});
