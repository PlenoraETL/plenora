import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { OPERATION_PANELS, PANEL_PARAMS } from '../../src/components/transforms/panels';
import { HANDLED_PARAM_TYPES } from '../../src/components/transforms/ParameterInput';
import {
  conditionsToSave, formulasToSave, gridConfig, gridError, gridRows, joinKeysPatch, keptColumnsPatch,
  lookupFromConditional, readFilterConditions, readFormulaItems, readJoinKeys, readKeptColumns,
  prettyName, stripJoinSuffix, validateConditions, validateFormulas, validateJoinKeys,
} from '../../src/components/transforms/panels/panelConfig';
import {
  applySelectColumns, joinColumnPlan, outputReferencesPatch, selectColumnPlan, usesJoinColumnPlan,
} from '../../src/utils/columnPlans';
import { bindingListResolution } from '../../src/utils/runtimeBindingEdges';
import { readValueMapping } from '../../src/utils/lookupMapping';

/**
 * Contract Fase 4 M4/M5 (correzione unica dopo la raccolta difetti): una
 * regola per classe, verificata su tutti i casi e sul registry vero.
 */
const registry = JSON.parse(fs.readFileSync(
  path.resolve(import.meta.dirname, '../../../backend/app/core/manipola/transforms_registry.json'), 'utf-8'));

describe('formato: un editor per ogni tipo, ogni parametro coperto', () => {
  it('every registry parameter type has a dedicated editor (no generic text fallback)', () => {
    const types = new Set(Object.values(registry.transforms).flatMap(t => (t.parameters || []).map(p => p.type)));
    expect([...types].filter(t => !HANDLED_PARAM_TYPES.includes(t))).toEqual([]);
  });

  it('every panel covers exactly the registry parameters of its operation', () => {
    for (const op of Object.keys(OPERATION_PANELS)) {
      const params = (registry.transforms[op]?.parameters || []).map(p => p.id).sort();
      expect([...PANEL_PARAMS[op]].sort(), op).toEqual(params);
    }
  });

  it('lookup mapping is read in every saved format', () => {
    const rows = [{ from: 'A', to: '1' }];
    expect(readValueMapping({ A: '1' })).toEqual(rows);
    expect(readValueMapping([{ old_name: 'A', new_name: '1' }])).toEqual(rows);
    expect(readValueMapping([{ from: 'A', to: '1' }])).toEqual(rows);
    expect(readValueMapping(null)).toEqual([]);
  });
});

describe('Filtra', () => {
  const cols = [{ name: 'periodo', id: 'c1' }, { name: 'importo', id: 'c2' }];

  it('reads the old single filter and resolves columns by id', () => {
    expect(readFilterConditions({ column: 'old', column_column_id: 'c1', operator: '!=', value: 'x' }, cols))
      .toEqual([{ column: 'periodo', column_id: 'c1', operator: '!=', value: 'x' }]);
    expect(readFilterConditions({ conditions: [], column: 'a' }, cols)).toEqual([]); // la lista vince
  });

  it('an empty-string value is a valid comparison; a missing column blocks the whole list', () => {
    expect(validateConditions([{ column: 'periodo', operator: '==', value: '' }])).toBeNull();
    expect(validateConditions([{ column: '', operator: '==', value: 'x' }])).toMatch(/colonna/);
    expect(validateConditions([{ column: '', operator: '==', value: '' }])).toBeNull(); // riga vuota ignorata
  });

  it('saves conditions with their column id and keeps unknown fields', () => {
    expect(conditionsToSave([
      { column: 'periodo', operator: 'in', value: 'A, B', note: 'x' },
      { column: 'importo', operator: 'isnull', value: 'ignored' },
      { column: '', operator: '==', value: '' },
    ], cols)).toEqual([
      { column: 'periodo', operator: 'in', note: 'x', column_id: 'c1', value: 'A, B' },
      { column: 'importo', operator: 'isnull', column_id: 'c2' },
    ]);
  });
});

describe('Formula', () => {
  it('reads the old single formula; an incomplete row blocks saving', () => {
    expect(readFormulaItems({ new_column: 'n', formula: 'a*2' })).toEqual([{ new_column: 'n', formula: 'a*2' }]);
    expect(validateFormulas([{ new_column: 'x', formula: '' }])).toMatch(/espressione/);
    expect(formulasToSave([{ new_column: ' x ', formula: 'a' }, { new_column: '', formula: '' }]))
      .toEqual([{ new_column: 'x', formula: 'a' }]);
  });
});

describe('Join', () => {
  const L = ['id', 'nome', 'v'];
  const R = ['ls_id', 'nome', 'w'];
  const lc = [{ name: 'id', id: 'l1' }, { name: 'nome', id: 'l2' }, { name: 'v', id: 'l3' }];

  it('reads old left_on/right_on and keys by id; writes aligned ids', () => {
    expect(readJoinKeys({ left_on: 'id', right_on: 'ls_id' })).toEqual([['id', 'ls_id']]);
    expect(readJoinKeys({ left_keys: ['old'], left_keys_column_ids: ['l1'], right_keys: ['ls_id'] }, lc, []))
      .toEqual([['id', 'ls_id']]);
    expect(validateJoinKeys([['id', '']])).toMatch(/entrambe/);
    const patch = joinKeysPatch([['id', 'ls_id']], lc, [{ name: 'ls_id' }]);
    expect(patch).toMatchObject({ left_keys: ['id'], right_keys: ['ls_id'], left_keys_column_ids: ['l1'], right_keys_column_ids: [null] });
    expect(patch.left_on).toBeUndefined();
  });

  it('kept columns carry their ids; all kept = null and no stale ids', () => {
    expect(keptColumnsPatch('left', lc, ['v'])).toEqual({ left_columns: ['v'], left_columns_column_ids: ['l3'] });
    expect(keptColumnsPatch('left', lc, L)).toEqual({ left_columns: null, left_columns_column_ids: undefined });
    expect(readKeptColumns({ left_columns: ['old'], left_columns_column_ids: ['l3'] }, 'left', lc)).toEqual(['v']);
  });

  it('the plan mirrors the backend: suffix, keep_left, keep_right, keys only', () => {
    expect(joinColumnPlan(L, R, ['id'], ['ls_id'], null, null, 'suffix').outputs)
      .toEqual(['id', 'nome_L', 'v_L', 'ls_id', 'nome_R', 'w_R']);
    expect(joinColumnPlan(L, R, ['id'], ['ls_id'], null, null, 'keep_left').outputs).toEqual(['id', 'nome', 'v', 'ls_id', 'w']);
    expect(joinColumnPlan(L, R, ['id'], ['ls_id'], null, null, 'keep_right').outputs).toEqual(['id', 'v', 'ls_id', 'nome', 'w']);
    expect(joinColumnPlan(L, R, ['id'], ['ls_id'], [], ['w'], 'keep_left').outputs).toEqual(['id', 'ls_id', 'w']);
    expect(joinColumnPlan(['k', 'a'], ['k', 'b'], ['k'], ['k'], null, null, 'keep_left').outputs).toEqual(['k', 'a', 'b']);
    expect(joinColumnPlan(L, R, ['id'], ['missing'], null, null, 'suffix').error).toBeTruthy();
    expect(usesJoinColumnPlan({ left_keys: ['a'] })).toBe(false);
    expect(usesJoinColumnPlan({ name_conflict: 'suffix' })).toBe(true);
  });
});

describe('Seleziona e rinomina e Output', () => {
  const up = [{ name: 'a', type: 'str', id: 'ia' }, { name: 'b', type: 'int', id: 'ib' }, { name: 'c', type: 'str' }];

  it('the plan mirrors the backend; missing names skipped, malformed entries always rejected', () => {
    expect(selectColumnPlan(['a', 'b', 'c'], [{ name: 'c', label: 'C' }, { name: 'a', keep: false }]).plan)
      .toEqual([['c', 'C'], ['b', 'b']]);
    expect(selectColumnPlan(['a'], [{ name: 'zz' }], { skipMissing: true }).plan).toEqual([['a', 'a']]);
    expect(selectColumnPlan(['a'], [null], { skipMissing: true }).error).toBeTruthy();
    expect(selectColumnPlan(['a'], [{ name: 'a', keep: 0 }], { skipMissing: true }).error).toBeTruthy();
  });

  it('grid rows keep missing entries, resolve renamed columns by id, empty label = same name', () => {
    const rows = gridRows(up, [
      { name: 'old_b', name_column_id: 'ib', label: 'B' }, { name: 'gone', label: 'G' }, { name: 'a', keep: false },
    ]);
    expect(rows.map(r => [r.name, r.keep, r.label, r.missing])).toEqual([
      ['b', true, 'B', false], ['gone', true, 'G', true], ['a', false, '', false], ['c', true, '', false],
    ]);
    expect(gridConfig(rows, up)).toEqual([
      { name: 'b', name_column_id: 'ib', keep: true, label: 'B' },
      { name: 'gone', keep: true, label: 'G' },
      { name: 'a', name_column_id: 'ia', keep: false },
      { name: 'c', keep: true },
    ]);
  });

  it('invalid grids are reported (and therefore not written)', () => {
    // Pipeline: la regola e' quella del backend (selectColumnPlan, verificata sui casi Python)
    expect(gridError([{ name: 'a', keep: true, label: 'x' }, { name: 'b', keep: true, label: 'x' }])).toMatch(/ripetuto/);
    expect(gridError([{ name: 'a', keep: true, label: 'x ' }])).toBeNull();
    expect(gridError([{ name: 'a', keep: true, label: '   ' }])).toBeTruthy();
    expect(gridError([{ name: 'a', keep: true, label: '' }])).toBeNull();
    expect(gridError([{ name: 'k', display: 't.a', keep: true, label: 'Canone mese' }], { identifier: true })).toMatch(/minuscole/);
    // Estrai: nomi ripetuti tra tabelle non bloccano (l'esecuzione li rinomina _2, _3)
    expect(gridError([{ name: 'k1', display: 't.id', keep: true, label: '' }, { name: 'k2', display: 'u.id', keep: true, label: '' }], { identifier: true }))
      .toBeNull();
    expect(prettyName('canone_mese_netto')).toBe('Canone mese netto');
    expect(prettyName('ID_contratto')).toBe('ID contratto');
    expect(stripJoinSuffix('nome_L_R')).toBe('nome');
  });

  it('Output prediction follows a column renamed upstream through its id', () => {
    const renamed = [{ name: 'b2', id: 'ib' }, { name: 'a', id: 'ia' }];
    expect(applySelectColumns(renamed, [{ name: 'b', name_column_id: 'ib', keep: false }], { skipMissing: true }).map(c => c.name))
      .toEqual(['a']);
    expect(applySelectColumns(renamed, [{ name: 'b', name_column_id: 'ib', label: 'B' }], { skipMissing: true }).map(c => c.name))
      .toEqual(['B', 'a']);
  });

  it('select_columns prediction skips missing names like the backend', () => {
    expect(applySelectColumns(up, [{ name: 'b', label: 'B' }, { name: 'gone' }], { skipMissing: true }).map(c => c.name))
      .toEqual(['B', 'a', 'c']);
  });

  // "Cosa esce dall'Output (selezione, poi GROUP BY)" lo prevede ora il server
  // (Blocco A, regola 4): i Carica leggono l'ingresso dalla fonte unica
  // (tests/shared/columnSource.contract), non piu' resultOutputColumns.

  it('renaming or dropping in the grid rewrites the other Output settings', () => {
    const data = {
      output_columns: [], group_by_columns: ['a', 'c'], geomColumn: 'c',
      aggregations: [{ column: 'b', function: 'sum' }], order_by_columns: [{ column: 'c', direction: 'asc' }],
    };
    const patch = outputReferencesPatch(data, [{ name: 'a' }, { name: 'b' }, { name: 'c' }], [{ name: 'a', label: 'A' }, { name: 'c', keep: false }]);
    expect(patch).toEqual({ group_by_columns: ['A'], order_by_columns: [], geomColumn: null });
  });
});

describe('input runtime e liste', () => {
  const node = (op, config) => ({ data: { transform_op: op, config } });

  it('a single-item list goes back to the single form, more items refuse the binding', () => {
    expect(bindingListResolution(node('filter', { conditions: [{ column: 'a', column_id: 'x', operator: '!=', value: 'q' }] }), 'value').patch)
      .toEqual({ conditions: undefined, column: 'a', operator: '!=', value: 'q', column_column_id: 'x' });
    expect(bindingListResolution(node('formula', { items: [{ new_column: 'n', formula: 'a' }] }), 'formula').patch)
      .toEqual({ items: undefined, new_column: 'n', formula: 'a' });
    expect(bindingListResolution(node('filter', { conditions: [{}, {}] }), 'value').refuse).toMatch(/2 condizioni/);
    expect(bindingListResolution(node('filter', { conditions: [] }), 'value').patch).toEqual({ conditions: undefined });
    expect(bindingListResolution(node('filter', { column: 'a' }), 'value')).toBeNull();
    expect(bindingListResolution(node('sort', { conditions: [{}, {}] }), 'value')).toBeNull();
  });
});

describe('suggerimento Lookup: solo se il risultato e\' identico', () => {
  const cols = [{ name: 'ctr_period', type: 'str' }, { name: 'flag', type: 'bool' }];
  const base = {
    column: 'ctr_period', output_column: 'fattore', default_value: '',
    conditions: [{ operator: '==', value: 'MONTH', result: '1' }, { operator: '==', value: 'YEAR', result: '12' }],
  };

  it('converts a correspondence table on a text column', () => {
    expect(lookupFromConditional(base, cols)).toEqual({
      column: 'ctr_period', column_column_id: undefined, output_column: 'fattore', unmatched: 'null',
      mapping: [{ from: 'MONTH', to: '1' }, { from: 'YEAR', to: '12' }],
    });
    expect(lookupFromConditional({ ...base, conditions: [{ value: 'A', result: 'x' }] }, cols))
      .toMatchObject({ unmatched: 'value', default: '' });
  });

  it('refuses every case where the result could differ', () => {
    const no = (patch) => expect(lookupFromConditional({ ...base, ...patch }, cols)).toBeNull();
    no({ conditions: [{ operator: '>', value: 'A', result: '1' }] });
    no({ conditions: [{ operator: '', value: 'A', result: '1' }] });
    no({ conditions: [{ operator: '==', value: '3', result: '1' }] });
    no({ conditions: [{ operator: '==', value: 'nan', result: '1' }] });
    no({ conditions: [{ operator: '==', value: '1_0', result: '1' }] });
    no({ conditions: [{ operator: '==', value: 'A', result: '1,5' }] });
    no({ conditions: [{ operator: '==', value: 'A', result: null }] });
    no({ conditions: [...base.conditions, { operator: '==', value: 'MONTH', result: '2' }] });
    no({ column: 'flag' });
    no({ output_column: 'ctr_period' });
    expect(lookupFromConditional(base, [])).toBeNull(); // tipo sconosciuto
  });
});
