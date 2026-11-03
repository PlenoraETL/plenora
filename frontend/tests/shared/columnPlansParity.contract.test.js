import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import {
  aggregationOutputType, joinColumnPlan, joinOutputPlan, legacyJoinPlan, resultGroupPlan, selectColumnPlan,
} from '../../src/utils/columnPlans';

/**
 * Parita' con il backend (Fase 4 M4/M5, cambio di approccio dopo quattro
 * giri di lettura): gli stessi casi, con i risultati GENERATI dalle funzioni
 * Python vere (backend/tests2/contracts/generate_column_plans_cases.py),
 * girano qui contro le copie JS. Una differenza e' un test rosso.
 */
const cases = JSON.parse(fs.readFileSync(
  path.resolve(import.meta.dirname, '../../../backend/tests2/contracts/column_plans_cases.json'), 'utf-8'));

const outcome = (r) => (r.error ? { error: true } : r);

describe('select_column_plan parity', () => {
  it.each(cases.select.map(c => [c.id, c]))('%s', (_, c) => {
    const r = selectColumnPlan(c.input.names, c.input.columns, { skipMissing: c.input.skip_missing });
    expect(outcome(r.error ? r : { plan: r.plan })).toEqual(c.expected);
  });
});

describe('join_column_plan parity', () => {
  it.each(cases.join.map(c => [c.id, c]))('%s', (_, c) => {
    const i = c.input;
    const r = joinColumnPlan(i.left, i.right, i.left_keys, i.right_keys, i.left_columns, i.right_columns, i.name_conflict);
    const o = joinOutputPlan(i.left, i.right, i.left_keys, i.right_keys, i.left_columns, i.right_columns, i.name_conflict);
    expect(outcome(r.error ? r : { plan: [Object.fromEntries(r.left), Object.fromEntries(r.right)], columns: o.columns }))
      .toEqual(c.expected);
  });
});

describe('result_group_plan parity', () => {
  it.each(cases.grouping.map(c => [c.id, c]))('%s', (_, c) => {
    const r = resultGroupPlan(c.input.node, c.input.names);
    expect(outcome(r.error ? r : { aggregations: r.aggregations, groups: r.groups })).toEqual(c.expected);
  });
});

describe('legacy_join_plan parity', () => {
  it.each(cases.join_legacy.map(c => [c.id, c]))('%s', (_, c) => {
    const i = c.input;
    const r = legacyJoinPlan(i.left, i.right, i.left_keys, i.right_keys);
    expect(outcome(r.error ? r : { columns: r.columns })).toEqual(c.expected);
  });
});

describe('aggregation_output_type parity', () => {
  it.each(cases.aggregation_types.map(c => [c.id, c]))('%s', (_, c) => {
    expect({ type: aggregationOutputType(c.input.func, c.input.source_type) }).toEqual(c.expected);
  });
});
