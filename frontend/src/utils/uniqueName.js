/**
 * uniqueName.js — nomi generati unici e stabili (Fase 5 T5, R8.19).
 *
 * Un nome proposto (`input_N`, `count_N`, la chiave di un input creato da un
 * parametro) e' il primo libero tra quelli GIA' USATI, non "quanti ce ne sono
 * + 1": aggiungendo due input e cancellando il primo, il nuovo non nasce
 * doppione del secondo. L'ordine di un nuovo input e' dopo il piu' alto, non
 * il conteggio (che dopo una cancellazione ripete un ordine esistente).
 */
import { slugify } from './fieldCodecs';

/**
 * Primo nome libero: `base` se libero (con `bareFirst`), poi `base_2`, ...
 * oppure, senza `bareFirst`, `base_1`, `base_2`, ...
 */
export function uniqueName(base, taken, { bareFirst = false, separator = '_' } = {}) {
  const used = new Set([...(taken || [])].filter(Boolean).map(String));
  if (bareFirst && !used.has(base)) return base;
  for (let n = bareFirst ? 2 : 1; ; n += 1) {
    const candidate = `${base}${separator}${n}`;
    if (!used.has(candidate)) return candidate;
  }
}

const projectInputs = (nodes) => (nodes || []).filter(n => n.type === 'project_input');

/**
 * Dati di un nuovo input di progetto: chiave unica (`input_N` libero, o
 * derivata da `label`), etichetta coerente, ordine dopo il piu' alto.
 */
export function newProjectInputData(nodes, { kind, label, ...rest } = {}) {
  const inputs = projectInputs(nodes);
  const keys = inputs.map(n => n.data?.input_key);
  const orders = inputs.map(n => n.data?.order).filter(o => Number.isInteger(o));
  const order = orders.length ? Math.max(...orders) + 1 : inputs.length;
  let input_key;
  let finalLabel = label;
  if (label) {
    input_key = uniqueName(slugify(label) || 'input', keys, { bareFirst: true });
  } else {
    input_key = uniqueName('input', keys);
    finalLabel = `Input ${input_key.slice('input_'.length)}`;
  }
  return {
    input_key,
    label: finalLabel,
    input_kind: kind,
    required: true,
    expected_columns: [],
    validation: {},
    bindings: [],
    order,
    ...rest,
  };
}
