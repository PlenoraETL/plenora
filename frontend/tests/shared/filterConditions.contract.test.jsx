import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, it, expect, afterEach, vi } from 'vitest';

import {
  FILTER_OPERATORS, addConditionGroup, addConditionToGroup, conditionGroups, describeConditionGroups,
  normalizeConditionGroups, operatorsFor,
} from '../../src/utils/filterConditions';
import { conditionsToSave, FILTER_OPERATORS as MANIPOLA_OPERATORS } from '../../src/components/transforms/panels/panelConfig';
import { transformSummary } from '../../src/components/canvas/nodeDescriptors';
import FilterPanel from '../../src/components/transforms/panels/FilterPanel';

/**
 * UI-3 (06/11/2026), U20: un solo catalogo degli operatori dei filtri
 * (Estrai e Manipola, con lo stesso nome) e gruppi E/O con lo stesso
 * formato nei due motori. FORMATO COMPATIBILE: i filtri salvati prima
 * (nessun `group`) si leggono e si salvano identici, tutti in E.
 */
afterEach(() => cleanup());

const REPO = path.resolve(import.meta.dirname, '../../..');
const backend = (rel) => fs.readFileSync(path.join(REPO, 'backend', rel), 'utf-8');
const quotedIn = (text) => [...text.matchAll(/"([^"]+)"/g)].map(m => m[1]);

describe('one operator catalog, same codes as the backend', () => {
  it('Estrai operators = backend WHERE operators and the canvas Filter model', () => {
    const where = backend('app/core/estrai/query_filters.py').match(/valid_operators = \{([\s\S]*?)\}/)[1];
    const model = backend('app/core/projects/canvas_models.py').match(/class Filter\(BaseModel\):[\s\S]*?operator: Literal\[([\s\S]*?)\]/)[1];
    const ours = operatorsFor('estrai').map(o => o.value).sort();
    expect(ours).toEqual(quotedIn(where).sort());
    expect(ours).toEqual(quotedIn(model).sort());
  });

  it('Manipola operators = backend Filtra operators', () => {
    const ops = backend('app/core/manipola/transforms/filtering.py').match(/FILTER_OPERATORS = frozenset\(\{([\s\S]*?)\}\)/)[1];
    expect(operatorsFor('manipola').map(o => o.value).sort()).toEqual(quotedIn(ops).sort());
    expect(MANIPOLA_OPERATORS.map(([v]) => v)).toEqual(operatorsFor('manipola').map(o => o.value));
  });

  it('an operator present in both engines has the same name in both', () => {
    for (const o of FILTER_OPERATORS) {
      expect(o.label, o.id).toMatch(/^[a-zà-ù ]+$/);
      expect(o.estrai || o.manipola, o.id).toBeTruthy();
    }
  });
});

describe('E/O groups: one rule, compatible format', () => {
  const legacy = [
    { column: 'a', operator: '=', value: '1' },
    { column: 'b', operator: 'IS NULL', value: '' },
  ];

  it('filters saved before the groups are read and saved identical (all in AND)', () => {
    expect(conditionGroups(legacy)).toHaveLength(1);
    const { list, moved } = normalizeConditionGroups(legacy);
    expect(list).toEqual(legacy);
    expect(moved).toEqual([0, 1]);
    expect(conditionsToSave([{ column: 'x', operator: '==', value: '1' }])).toEqual([{ column: 'x', operator: '==', value: '1', column_id: undefined }]);
    expect(transformSummary('filter', { conditions: [{ column: 'x', operator: '==', value: 1 }, { column: 'y', operator: 'isnull' }] }))
      .not.toMatch(/\(|\bo\b/);
  });

  it('groups are renumbered in display order, rows of a group stay together, bindings follow', () => {
    const list = [
      { column: 'a', group: 5 }, { column: 'b', group: 2 }, { column: 'c', group: 5 },
    ];
    const { list: out, moved } = normalizeConditionGroups(list);
    expect(out).toEqual([{ column: 'a', group: 0 }, { column: 'c', group: 0 }, { column: 'b', group: 1 }]);
    expect(moved).toEqual([0, 2, 1]);
  });

  it('adding a condition or a group keeps the rows of a group together', () => {
    let list = [{ column: 'a' }];
    list = addConditionGroup(list, { column: '' });
    list = addConditionToGroup(list, 0, { column: 'z' });
    expect(list.map(r => [r.column, r.group ?? 0])).toEqual([['a', 0], ['z', 0], ['', 1]]);
    expect(describeConditionGroups([{ c: 'A' }, { c: 'B' }, { c: 'C', group: 1 }], r => r.c, { and: ' AND ', or: ' OR ' }))
      .toBe('(A AND B) OR (C)');
  });

  it('Manipola Filtra: «Aggiungi un gruppo O» writes the groups; one group writes the old format', () => {
    const updateConfig = vi.fn();
    const columns = [{ name: 'stato', type: 'str' }];
    const config = { conditions: [{ column: 'stato', operator: '==', value: 'aperto' }] };
    render(<FilterPanel config={config} updateConfig={updateConfig} inputColumns={columns} />);
    fireEvent.click(screen.getByRole('button', { name: /Aggiungi un gruppo O/ }));
    const selects = screen.getAllByRole('combobox', { name: 'Colonna' });
    fireEvent.change(selects[1], { target: { value: 'stato' } });
    const last = updateConfig.mock.calls.at(-1)[0];
    expect(last.conditions.map(c => [c.column, c.group])).toEqual([['stato', 0], ['stato', 1]]);
    expect(screen.getByText('oppure (O)')).toBeTruthy();
  });
});
