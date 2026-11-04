import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { joinColumnPlan, selectColumnPlan } from '../../src/utils/columnPlans';

/**
 * Parita' con il backend (Fase 4 M4/M5, cambio di approccio dopo quattro
 * giri di lettura): gli stessi casi, con i risultati GENERATI dalle funzioni
 * Python vere (backend/tests2/contracts/generate_column_plans_cases.py),
 * girano qui contro le copie JS. Una differenza e' un test rosso.
 *
 * Dopo la F2 del Blocco A (regola 4) il frontend non prevede piu' le colonne
 * a valle: le prevede il server (utils/columnSource). Restano le copie usate
 * dai pannelli sulla configurazione che l'utente sta scrivendo
 * (select_column_plan per la griglia, join_column_plan per l'anteprima del
 * Join). Le copie di prepared_join_plan, legacy_join_plan, result_group_plan,
 * aggregation_output_type e delle tabelle dei tipi sono uscite insieme alla
 * propagazione: qui non si verificano piu' (le verifica il backend, che e'
 * l'unica fonte).
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
    const expected = c.expected.error ? { error: true } : { plan: c.expected.plan };
    expect(outcome(r.error ? r : { plan: [Object.fromEntries(r.left), Object.fromEntries(r.right)] }))
      .toEqual(expected);
  });
});
