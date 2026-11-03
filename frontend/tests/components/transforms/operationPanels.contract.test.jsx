import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { storeState, registryValue } = vi.hoisted(() => ({
  storeState: { current: {} },
  registryValue: { current: null },
}));

vi.mock('@xyflow/react', () => ({
  Position: { Left: 'left', Right: 'right', Top: 'top' },
  useReactFlow: () => ({ getEdges: () => storeState.current.edges || [], getNodes: () => storeState.current.nodes || [] }),
  useNodeId: () => null,
  Handle: () => null,
}));
vi.mock('../../../src/store/useCanvasStore', () => {
  const hook = (selector) => selector(storeState.current);
  hook.getState = () => storeState.current;
  return { default: hook };
});
vi.mock('../../../src/context/ConfirmContext', () => ({ useConfirm: () => async () => true }));
vi.mock('../../../src/components/modals/RunHistoryModal', () => ({ default: () => null }));
vi.mock('../../../src/utils/transformRegistry', () => ({
  fetchTransformRegistry: vi.fn(async () => registryValue.current),
  getCachedTransformRegistry: () => registryValue.current,
  isMultiInputOp: (op) => op === 'join',
}));

import TransformNode from '../../../src/components/transforms/TransformNode';
import FilterPanel from '../../../src/components/transforms/panels/FilterPanel';
import FormulaPanel from '../../../src/components/transforms/panels/FormulaPanel';
import JoinPanel from '../../../src/components/transforms/panels/JoinPanel';
import SelectColumnsPanel from '../../../src/components/transforms/panels/SelectColumnsPanel';

/** Contract Fase 4 M4/M5: pannelli dedicati nel pannello laterale. */
const cols = [{ name: 'ctr_period', type: 'str' }, { name: 'importo', type: 'real' }];

describe('FilterPanel', () => {
  it('converts the old single filter; an incomplete row blocks saving, an empty value does not', () => {
    const updateConfig = vi.fn();
    render(<FilterPanel config={{ column: 'ctr_period', operator: '!=', value: 'UNIQUE' }} updateConfig={updateConfig} inputColumns={cols} />);
    fireEvent.click(screen.getByText(/Aggiungi condizione/));
    expect(updateConfig).not.toHaveBeenCalled(); // la riga nuova e' vuota: ignorata
    fireEvent.change(screen.getAllByLabelText('Valore')[1], { target: { value: 'MONTH, YEAR' } });
    expect(screen.getByRole('alert').textContent).toMatch(/colonna/); // manca la colonna: non salva
    expect(updateConfig).not.toHaveBeenCalled();
    fireEvent.change(screen.getAllByLabelText('Operatore')[1], { target: { value: 'in' } });
    fireEvent.change(screen.getAllByLabelText('Colonna')[1], { target: { value: 'ctr_period' } });
    expect(updateConfig).toHaveBeenLastCalledWith(expect.objectContaining({
      conditions: [
        { column: 'ctr_period', operator: '!=', value: 'UNIQUE' },
        { column: 'ctr_period', operator: 'in', value: 'MONTH, YEAR' },
      ],
      column: undefined, operator: undefined, value: undefined,
    }));
    fireEvent.change(screen.getAllByLabelText('Valore')[0], { target: { value: '' } });
    expect(updateConfig.mock.calls.at(-1)[0].conditions[0]).toEqual({ column: 'ctr_period', operator: '!=', value: '' });
  });
});

describe('FormulaPanel', () => {
  it('adds a second calculated column and inserts a column name on click', () => {
    const updateConfig = vi.fn();
    render(<FormulaPanel config={{ items: [{ new_column: 'netto', formula: 'importo / 12' }] }} updateConfig={updateConfig} inputColumns={cols} />);
    fireEvent.click(screen.getByText(/Aggiungi colonna calcolata/));
    fireEvent.change(screen.getAllByLabelText('Nome nuova colonna')[1], { target: { value: 'lordo' } });
    fireEvent.focus(screen.getAllByLabelText('Espressione')[1]);
    fireEvent.click(screen.getByRole('button', { name: 'importo' }));
    expect(updateConfig).toHaveBeenLastCalledWith(expect.objectContaining({
      items: [{ new_column: 'netto', formula: 'importo / 12' }, { new_column: 'lordo', formula: 'importo' }],
    }));
  });
});

describe('JoinPanel', () => {
  const left = [{ name: 'ctr_ls_id' }, { name: 'nome' }];
  const right = [{ name: 'ls_id' }, { name: 'nome' }, { name: 'pr_id' }];

  it('an old join keeps its behaviour until the user chooses; choosing switches to the new path', () => {
    const updateConfig = vi.fn();
    render(<JoinPanel config={{ left_keys: ['ctr_ls_id'], right_keys: ['ls_id'] }} updateConfig={updateConfig}
      leftColumns={left} rightColumns={right} />);
    expect(screen.getByText(/comportamento di prima/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Tieni quella di sinistra'));
    expect(updateConfig).toHaveBeenLastCalledWith(expect.objectContaining({
      name_conflict: 'keep_left', left_keys: ['ctr_ls_id'], right_keys: ['ls_id'],
    }));
  });

  it('shows the result computed like the backend and flags same-name columns', () => {
    render(<JoinPanel config={{ left_keys: ['ctr_ls_id'], right_keys: ['ls_id'], name_conflict: 'keep_left', right_columns: ['nome'] }}
      updateConfig={vi.fn()} leftColumns={left} rightColumns={right} />);
    expect(screen.getByText(/3 colonne/)).toBeTruthy(); // ctr_ls_id, nome, ls_id
    expect(screen.getAllByText('nome uguale').length).toBeGreaterThan(0);
  });
});

describe('SelectColumnsPanel', () => {
  it('renames, drops and reorders in one list', () => {
    const updateConfig = vi.fn();
    render(<SelectColumnsPanel config={{ columns: [] }} updateConfig={updateConfig} inputColumns={cols} />);
    fireEvent.change(screen.getByLabelText('Nome in uscita di importo'), { target: { value: 'Importo' } });
    expect(updateConfig).toHaveBeenLastCalledWith({ columns: [{ name: 'ctr_period', keep: true }, { name: 'importo', keep: true, label: 'Importo' }] });
    fireEvent.click(screen.getByLabelText('Sposta su importo'));
    expect(updateConfig.mock.calls.at(-1)[0].columns.map(c => c.name)).toEqual(['importo', 'ctr_period']);
  });
});

describe('TransformNode in the side panel', () => {
  const registry = {
    categories: { base: { id: 'base', name: 'Base', icon: '📊' } },
    transforms: {
      filter: { name: 'Filtra', category: 'base', icon: '🔍', inputs: 'single', parameters: [{ id: 'column', type: 'column', label: 'Colonna' }] },
      conditional: { name: 'Condizionale', category: 'base', icon: '🔀', inputs: 'single', parameters: [] },
      lookup: { name: 'Mappa valori', category: 'base', icon: '🗺️', inputs: 'single', parameters: [] },
    },
  };
  const updateNodeData = vi.fn();
  beforeEach(() => {
    registryValue.current = registry;
    updateNodeData.mockReset();
    storeState.current = {
      nodes: [], edges: [], isReadOnly: false, projectId: 'p',
      updateNodeData, patchNodeConfig: vi.fn(), deleteNode: vi.fn(),
      editorActionsRef: { current: {} },
    };
  });

  it('has no old card header or category tabs, and uses the dedicated panel', () => {
    render(<TransformNode id="t1" inInspector data={{ transform_op: 'filter', config: {}, inputColumns: cols }} />);
    expect(screen.queryByText('Trasformazione')).toBeNull();
    expect(screen.queryByRole('button', { name: /Spaziale/ })).toBeNull();
    expect(screen.getByText(/Aggiungi condizione/)).toBeTruthy();
  });

  it('keeps the generic form when runtime inputs are bound to its parameters', () => {
    storeState.current.edges = [{ id: 'b', source: 'in', target: 't1', targetHandle: 'parameter:value', data: { edge_kind: 'parameter_binding' } }];
    render(<TransformNode id="t1" inInspector data={{ transform_op: 'filter', config: {}, inputColumns: cols }} />);
    expect(screen.queryByText(/Aggiungi condizione/)).toBeNull();
    expect(screen.getByText(/input runtime collegati/)).toBeTruthy();
  });

  it('suggests Mappa valori on a Case When that is a correspondence table, and converts it', () => {
    const config = {
      column: 'ctr_period', output_column: 'fattore', default_value: '',
      conditions: [{ operator: '==', value: 'MONTH', result: '1' }, { operator: '==', value: 'YEAR', result: '12' }],
    };
    render(<TransformNode id="t1" inInspector data={{ transform_op: 'conditional', config, inputColumns: cols }} />);
    fireEvent.click(screen.getByRole('button', { name: /Trasforma in Mappa valori/ }));
    expect(updateNodeData).toHaveBeenCalledWith('t1', expect.objectContaining({
      transform_op: 'lookup',
      config: expect.objectContaining({ column: 'ctr_period', output_column: 'fattore', unmatched: 'null' }),
    }));
  });
});
