import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, beforeEach } from 'vitest';
import useCanvasStore from '../../src/store/useCanvasStore';
import DynamicConfig from '../../src/components/transforms/DynamicConfig';

/**
 * Contract Fase 4 M4 (conferma): un input runtime e una lista che sostituisce
 * il suo parametro non convivono, su OGNI percorso di collegamento
 * (trascinamento, menu del parametro, "crea input": tutti passano da
 * store.prepareParameterBinding) e nel form generico.
 */
const filterNode = (config) => ({ id: 'f', type: 'transform', position: { x: 0, y: 0 }, data: { transform_op: 'filter', config } });

describe('store.prepareParameterBinding', () => {
  beforeEach(() => { useCanvasStore.getState().reset(); useCanvasStore.getState().setReadOnly(false); });

  it('one condition goes back to the single filter, several refuse', () => {
    const s = useCanvasStore.getState();
    s.loadGraph([filterNode({ conditions: [{ column: 'a', operator: '==', value: 'x' }] })], []);
    expect(s.prepareParameterBinding('f', 'value')).toEqual({ ok: true, message: null });
    expect(useCanvasStore.getState().nodes[0].data.config).toMatchObject({ column: 'a', operator: '==', value: 'x' });
    expect(useCanvasStore.getState().nodes[0].data.config.conditions).toBeUndefined();

    s.loadGraph([filterNode({ conditions: [{ column: 'a', operator: '==' }, { column: 'b', operator: '==' }] })], []);
    const res = s.prepareParameterBinding('f', 'value');
    expect(res.ok).toBe(false);
    expect(res.message).toMatch(/2 condizioni/);
  });
});

describe('one Ctrl+Z undoes conversion and binding together', () => {
  beforeEach(() => { useCanvasStore.getState().reset(); useCanvasStore.getState().setReadOnly(false); });

  it('restores the list and removes the edge', () => {
    const s = useCanvasStore.getState();
    s.loadGraph([filterNode({ conditions: [{ column: 'a', operator: '==', value: 'x' }] })], []);
    s.prepareParameterBinding('f', 'value');
    s.addEdges({ id: 'b', source: 'in', target: 'f', targetHandle: 'parameter:value', data: { edge_kind: 'parameter_binding' } });
    s.undo();
    const st = useCanvasStore.getState();
    expect(st.edges).toEqual([]);
    expect(st.nodes[0].data.config.conditions).toHaveLength(1);
  });
});

describe('generic form with a bound parameter', () => {
  const registry = {
    filter: {
      name: 'Filtra', parameters: [
        { id: 'value', type: 'string', label: 'Valore' },
        { id: 'conditions', type: 'filter_conditions', label: 'Condizioni AND' },
      ],
    },
  };

  it('hides the list that would override the binding', () => {
    useCanvasStore.getState().loadGraph([filterNode({})], [
      { id: 'b', source: 'in', target: 'f', targetHandle: 'parameter:value', data: { edge_kind: 'parameter_binding', target_parameter: 'value' } },
    ]);
    render(<DynamicConfig transformId="filter" config={{}} updateConfig={() => {}} registry={registry} targetNodeId="f" />);
    expect(screen.queryByText(/Condizioni AND/)).toBeNull();
    expect(screen.getByText('Valore')).toBeTruthy();
  });

  it('shows it when nothing is bound', () => {
    useCanvasStore.getState().loadGraph([filterNode({})], []);
    render(<DynamicConfig transformId="filter" config={{}} updateConfig={() => {}} registry={registry} targetNodeId="f" />);
    expect(screen.getByText(/Condizioni AND/)).toBeTruthy();
  });
});
