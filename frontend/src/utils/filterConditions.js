/**
 * filterConditions — UNA regola per i filtri di Estrai (WHERE della tabella,
 * FilterModal) e di Manipola (passo Filtra, FilterPanel) (UI-3, U20).
 *
 * Prima: Estrai mostrava gli operatori SQL («LIKE», «IS NULL»), Manipola
 * parole italiane; nessuno dei due ammetteva O. Ora:
 *
 *  - FILTER_OPERATORS: un solo nome per ogni operatore, con il codice che
 *    ciascun motore salva (`estrai`: SQL del backend, query_filters;
 *    `manipola`: transforms/filtering) e i tipi di colonna a cui si applica
 *    in Estrai. Il test tests/shared/filterConditions.contract.test.js
 *    confronta i codici con il backend.
 *
 *  - GRUPPI E/O: ogni condizione puo' avere `group` (intero >= 0, assente
 *    = 0). Le condizioni dello stesso gruppo sono in E; i gruppi in O:
 *    (g0) O (g1) O ... Il backend usa la stessa regola (Estrai e Manipola).
 *    FORMATO COMPATIBILE: la lista resta piatta (gli input runtime
 *    collegati a `filters.<i>.value` restano validi) e con un solo gruppo
 *    `group` NON si scrive: i progetti esistenti si leggono e si salvano
 *    come prima (tutte in E).
 */

export const FILTER_OPERATORS = Object.freeze([
  { id: 'eq', label: 'è uguale a', estrai: '=', manipola: '==', estraiTypes: ['all'] },
  { id: 'ne', label: 'è diverso da', estrai: '!=', manipola: '!=', estraiTypes: ['all'] },
  { id: 'gt', label: 'è maggiore di', estrai: '>', manipola: '>', estraiTypes: ['int', 'real', 'date'] },
  { id: 'ge', label: 'è maggiore o uguale a', estrai: '>=', manipola: '>=', estraiTypes: ['int', 'real', 'date'] },
  { id: 'lt', label: 'è minore di', estrai: '<', manipola: '<', estraiTypes: ['int', 'real', 'date'] },
  { id: 'le', label: 'è minore o uguale a', estrai: '<=', manipola: '<=', estraiTypes: ['int', 'real', 'date'] },
  { id: 'contains', label: 'contiene', estrai: 'LIKE', manipola: 'contains', estraiTypes: ['str'] },
  { id: 'not_contains', label: 'non contiene', estrai: 'NOT LIKE', manipola: null, estraiTypes: ['str'] },
  { id: 'starts', label: 'inizia con', estrai: null, manipola: 'startswith' },
  { id: 'ends', label: 'finisce con', estrai: null, manipola: 'endswith' },
  { id: 'in', label: 'è uno di', estrai: 'IN', manipola: 'in', estraiTypes: ['all'], list: true },
  { id: 'not_in', label: 'non è uno di', estrai: 'NOT IN', manipola: 'not_in', estraiTypes: ['all'], list: true },
  { id: 'null', label: 'è vuoto', estrai: 'IS NULL', manipola: 'isnull', estraiTypes: ['all'], noValue: true },
  { id: 'not_null', label: 'non è vuoto', estrai: 'IS NOT NULL', manipola: 'notnull', estraiTypes: ['all'], noValue: true },
  { id: 'between', label: 'è compreso tra', estrai: 'BETWEEN', manipola: null, estraiTypes: ['int', 'real', 'date'] },
]);

/** Operatori di un motore: [{ value, label, ... }] nell'ordine del catalogo. */
export const operatorsFor = (engine) => FILTER_OPERATORS
  .filter(o => o[engine])
  .map(o => ({ ...o, value: o[engine] }));


const groupOf = (row) => {
  const g = Number(row?.group);
  return Number.isInteger(g) && g >= 0 ? g : 0;
};

/**
 * Gruppi nell'ordine di prima apparizione: [{ group, items: [{ row, index }] }],
 * `index` = posizione nella lista piatta.
 */
export function conditionGroups(list = []) {
  const order = [];
  const byGroup = new Map();
  list.forEach((row, index) => {
    const g = groupOf(row);
    if (!byGroup.has(g)) { byGroup.set(g, []); order.push(g); }
    byGroup.get(g).push({ row, index });
  });
  return order.map(g => ({ group: g, items: byGroup.get(g) }));
}

/**
 * Lista da salvare: gruppi rinumerati 0..k-1 nell'ordine in cui si vedono,
 * condizioni dello stesso gruppo vicine. Con un solo gruppo `group` si toglie
 * (formato di prima). `moved[i]` = nuova posizione della condizione i (per
 * spostare anche gli input runtime collegati a `filters.<i>.value`).
 */
export function normalizeConditionGroups(list = []) {
  const groups = conditionGroups(list);
  const single = groups.length <= 1;
  const out = [];
  const moved = new Array(list.length);
  groups.forEach((g, gi) => g.items.forEach(({ row, index }) => {
    moved[index] = out.length;
    // eslint-disable-next-line no-unused-vars
    const { group, ...rest } = row || {};
    out.push(single ? rest : { ...rest, group: gi });
  }));
  return { list: out, moved };
}

/** Nuovo gruppo O in fondo, con una condizione vuota. */
export function addConditionGroup(list, emptyRow) {
  const next = list.reduce((m, r) => Math.max(m, groupOf(r)), -1) + 1;
  return [...list, { ...emptyRow, group: next }];
}

/** Condizione vuota in coda a un gruppo (resta vicina alle sue compagne). */
export function addConditionToGroup(list, group, emptyRow) {
  const last = list.reduce((pos, r, i) => (groupOf(r) === group ? i : pos), list.length - 1);
  const row = { ...emptyRow, group };
  return [...list.slice(0, last + 1), row, ...list.slice(last + 1)];
}

/** Testo leggibile di tutti i gruppi: «A E B» / «(A E B) O (C)». */
export function describeConditionGroups(list, describeRow, { and = ' E ', or = ' O ' } = {}) {
  const groups = conditionGroups(list).map(g => g.items.map(({ row, index }) => describeRow(row, index)).filter(Boolean))
    .filter(parts => parts.length);
  if (groups.length <= 1) return (groups[0] || []).join(and);
  return groups.map(parts => `(${parts.join(and)})`).join(or);
}
