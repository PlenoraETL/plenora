/**
 * Contract test — graphUtils (FE-NEXT-1, opzionale).
 *
 * Pure functions estratte dalla duplicazione in validate{Estrai,Manipola}Canvas:
 *  - detectCycle(nodes, edges) — DFS bianco/grigio/nero
 */

import { describe, it, expect } from 'vitest';
import { detectCycle } from '../../src/utils/graphUtils';


describe('detectCycle', () => {
  it('grafo vuoto -> false', () => {
    expect(detectCycle([], [])).toBe(false);
  });

  it('grafo lineare a -> b -> c -> false', () => {
    const nodes = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    const edges = [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }];
    expect(detectCycle(nodes, edges)).toBe(false);
  });

  it('ciclo diretto a -> b -> a -> true', () => {
    const nodes = [{ id: 'a' }, { id: 'b' }];
    const edges = [{ source: 'a', target: 'b' }, { source: 'b', target: 'a' }];
    expect(detectCycle(nodes, edges)).toBe(true);
  });

  it('ciclo indiretto a -> b -> c -> a -> true', () => {
    const nodes = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      { source: 'c', target: 'a' },
    ];
    expect(detectCycle(nodes, edges)).toBe(true);
  });

  it('self-loop a -> a -> true', () => {
    expect(detectCycle([{ id: 'a' }], [{ source: 'a', target: 'a' }])).toBe(true);
  });

  it('DAG con diamond (a -> b, a -> c, b -> d, c -> d) -> false', () => {
    const nodes = ['a', 'b', 'c', 'd'].map(id => ({ id }));
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'a', target: 'c' },
      { source: 'b', target: 'd' },
      { source: 'c', target: 'd' },
    ];
    expect(detectCycle(nodes, edges)).toBe(false);
  });

  it('grafo con due componenti, una ciclica -> true', () => {
    const nodes = ['a', 'b', 'c', 'd'].map(id => ({ id }));
    const edges = [
      { source: 'a', target: 'b' }, // componente 1: a->b
      { source: 'c', target: 'd' }, // componente 2: c<->d ciclico
      { source: 'd', target: 'c' },
    ];
    expect(detectCycle(nodes, edges)).toBe(true);
  });

  it('edge che riferisce nodo inesistente -> non crasha, ritorna false', () => {
    const nodes = [{ id: 'a' }];
    const edges = [{ source: 'fantasma', target: 'a' }];
    expect(detectCycle(nodes, edges)).toBe(false);
  });
});


