/**
 * Contract test — graphUtils (FE-NEXT-1, opzionale).
 *
 * Pure functions estratte dalla duplicazione in validate{Estrai,Manipola}Canvas:
 *  - detectCycle(nodes, edges) — DFS bianco/grigio/nero
 *  - getNodeName(node, registry?) — switch su node.type per messaggi UI
 */

import { describe, it, expect } from 'vitest';
import { detectCycle, getNodeName } from '../../src/utils/graphUtils';


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


describe('getNodeName', () => {
  it('null -> "?"', () => {
    expect(getNodeName(null)).toBe('?');
  });

  it.each([
    ['tableNode', { table_name: 'Customers' }, 'Customers'],
    ['tableNode', { alias: 'C', table_name: 'Customers' }, 'C'],
    ['datasourceNode', { name: 'CSV1' }, 'CSV1'],
    ['featureLayerNode', { name: 'Roads' }, 'Roads'],
    ['resultNode', {}, 'Output'],
    ['project_source', { table_name: 'orders' }, 'orders'],
    ['file_source', { fileName: 'data.csv' }, 'data.csv'],
    ['staticValues', { alias: 'Costanti' }, 'Costanti'],
    ['result', {}, 'Output'],
    ['multi_join', {}, 'Multi-Join'],
  ])('%s -> %s', (type, data, expected) => {
    expect(getNodeName({ type, data })).toBe(expected);
  });

  it('carica con dest_type=arcgis -> "Carica ArcGIS"', () => {
    expect(getNodeName({ type: 'carica', data: { dest_type: 'arcgis' } })).toBe('Carica ArcGIS');
  });

  it('carica senza dest_type -> "Carica DB"', () => {
    expect(getNodeName({ type: 'carica', data: {} })).toBe('Carica DB');
  });

  it('transform: senza registry -> opId', () => {
    expect(getNodeName({ type: 'transform', data: { transform_op: 'filter' } })).toBe('filter');
  });

  it('transform: con registry -> nome leggibile', () => {
    const registry = { filter: { name: 'Filtra righe' } };
    expect(getNodeName({ type: 'transform', data: { transform_op: 'filter' } }, registry))
      .toBe('Filtra righe');
  });

  it('transform: opId in registry senza name -> opId fallback', () => {
    const registry = { filter: {} };
    expect(getNodeName({ type: 'transform', data: { transform_op: 'filter' } }, registry))
      .toBe('filter');
  });

  it('transform senza transform_op -> "Trasformazione"', () => {
    expect(getNodeName({ type: 'transform', data: {} })).toBe('Trasformazione');
  });

  it('tipo sconosciuto -> ritorna il type stesso', () => {
    expect(getNodeName({ type: 'mystery_node', id: 'x', data: {} })).toBe('mystery_node');
  });

  it('tipo sconosciuto + senza type -> id', () => {
    expect(getNodeName({ id: 'x', data: {} })).toBe('x');
  });
});
