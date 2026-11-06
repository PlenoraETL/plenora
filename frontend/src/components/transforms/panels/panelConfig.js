/**
 * panelConfig — lettura e scrittura della configurazione per i pannelli
 * dedicati (Fase 4, M4/M5): Filtra, Formula, Join, Seleziona e rinomina,
 * suggerimento Lookup. Funzioni pure, testate.
 *
 * Regole comuni (una per classe di difetti):
 *  - FORMATO: un pannello legge anche il formato vecchio (filtro singolo,
 *    formula singola, left_on/right_on) e scrive solo quello nuovo,
 *    ripulendo i campi vecchi;
 *  - LISTA VALIDA: si scrive solo se l'intera lista e' valida (validate*);
 *    altrimenti la configurazione resta l'ultima valida e il pannello dice
 *    cosa manca. Le righe del tutto vuote non contano (un "＋ Aggiungi"
 *    non blocca le altre modifiche);
 *  - RIFERIMENTI: ogni colonna scritta porta il suo id (column_id,
 *    name_column_id, *_column_ids) e in lettura l'id vince sul nome, come
 *    nel backend: una colonna rinominata a monte resta agganciata;
 *  - ALTRI CAMPI: le voci conservano i campi che il pannello non gestisce.
 */

import { isPythonFloat, readValueMapping } from '../../../utils/lookupMapping';
import { selectColumnPlan } from '../../../utils/columnPlans';
import { joinTypeOptions } from '../../../utils/joinTypes';
import { normalizeConditionGroups, operatorsFor } from '../../../utils/filterConditions';

export { readValueMapping };
// ── Colonne e riferimenti ────────────────────────────────────────────────
export const columnId = (col) => col?.id || col?.column_id || null;

/** Id allineati ai nomi (null dove la colonna non ha id): stessa posizione. */
const idsFor = (names, columns) =>
  names.map(n => columnId(columns.find(c => c.name === n)));

/** Nome attuale di una colonna: prima per id (sopravvive alle rinomine), poi per nome. */
function resolveName(columns, id, name) {
  if (id) {
    const byId = columns.find(c => columnId(c) === id);
    if (byId) return byId.name;
  }
  return name;
}

const isBlank = (v) => v === undefined || v === null || String(v).trim() === '';

// ── Filtra ───────────────────────────────────────────────────────────────
// Nomi dal catalogo unico dei filtri di Estrai e Manipola (utils/filterConditions, U20).
export const FILTER_OPERATORS = operatorsFor('manipola').map(o => [o.value, o.label]);
export const NO_VALUE_OPERATORS = ['isnull', 'notnull'];
export const LIST_OPERATORS = ['in', 'not_in'];

export const emptyCondition = () => ({ column: '', operator: '==', value: '' });
const isEmptyCondition = (c) => isBlank(c?.column) && isBlank(c?.value);

/** Condizioni salvate, con i nomi risolti per id. Legge anche il filtro singolo. */
export function readFilterConditions(config = {}, columns = []) {
  const list = Array.isArray(config.conditions)
    ? config.conditions
    : (config.column
      ? [{ column: config.column, column_id: config.column_column_id, operator: config.operator || '==', value: config.value ?? '' }]
      : []);
  return list.map(c => ({ ...c, column: resolveName(columns, c.column_id, c.column) }));
}

/**
 * Una condizione e' valida con colonna e operatore. Il valore puo' essere
 * vuoto: `colonna == ""` e' un confronto legittimo (il backend lo esegue).
 */
export function validateConditions(rows) {
  const bad = rows.findIndex(c => !isEmptyCondition(c) && (isBlank(c.column) || isBlank(c.operator)));
  return bad < 0 ? null : `Condizione ${bad + 1}: scegli la colonna`;
}

export function conditionsToSave(rows, columns = []) {
  const kept = rows.filter(c => !isEmptyCondition(c)).map((c) => {
    const { value, ...rest } = c;
    const out = { ...rest, column_id: columnId(columns.find(col => col.name === c.column)) || c.column_id || undefined };
    return NO_VALUE_OPERATORS.includes(c.operator) ? out : { ...out, value: value ?? '' };
  });
  // Gruppi E/O rinumerati; un solo gruppo = formato di prima (U20).
  return normalizeConditionGroups(kept).list;
}

export const FILTER_LEGACY_CLEAR = { column: undefined, operator: undefined, value: undefined, column_column_id: undefined };

// ── Formula ──────────────────────────────────────────────────────────────
export const emptyFormula = () => ({ new_column: '', formula: '' });
const isEmptyFormula = (i) => isBlank(i?.new_column) && isBlank(i?.formula);

export function readFormulaItems(config = {}) {
  if (Array.isArray(config.items)) return config.items;
  if (config.formula) return [{ new_column: config.new_column || 'calculated', formula: config.formula }];
  return [];
}

export function validateFormulas(rows) {
  const bad = rows.findIndex(i => !isEmptyFormula(i) && (isBlank(i.new_column) || isBlank(i.formula)));
  if (bad < 0) return null;
  return `Formula ${bad + 1}: ${isBlank(rows[bad].new_column) ? 'manca il nome della colonna' : "manca l'espressione"}`;
}

export const formulasToSave = (rows) =>
  rows.filter(i => !isEmptyFormula(i)).map(i => ({ ...i, new_column: String(i.new_column).trim() }));

export const FORMULA_LEGACY_CLEAR = { new_column: undefined, formula: undefined, formula_column_refs: undefined };

// ── Join ─────────────────────────────────────────────────────────────────
// Dal catalogo unico dei tre editor (utils/joinTypes, U21).
export const JOIN_TYPES = joinTypeOptions('manipola');
export const NAME_CONFLICTS = [
  ['keep_left', 'Tieni quella di sinistra'],
  ['keep_right', 'Tieni quella di destra'],
  ['suffix', 'Tienile entrambe (_L a sinistra, _R a destra)'],
];

/** Coppie di chiavi [sinistra, destra] con i nomi risolti per id; anche left_on/right_on. */
export function readJoinKeys(config = {}, leftColumns = [], rightColumns = []) {
  const left = Array.isArray(config.left_keys) ? config.left_keys : (config.left_on ? [config.left_on] : []);
  const right = Array.isArray(config.right_keys) ? config.right_keys : (config.right_on ? [config.right_on] : []);
  const lids = config.left_keys_column_ids || [];
  const rids = config.right_keys_column_ids || [];
  const n = Math.max(left.length, right.length);
  return Array.from({ length: n }, (_, i) => [
    resolveName(leftColumns, lids[i], left[i] || ''),
    resolveName(rightColumns, rids[i], right[i] || ''),
  ]);
}

export function validateJoinKeys(pairs) {
  const bad = pairs.findIndex(([l, r]) => Boolean(l) !== Boolean(r));
  return bad < 0 ? null : `Chiave ${bad + 1}: scegli entrambe le colonne`;
}

export function joinKeysPatch(pairs, leftColumns, rightColumns) {
  const complete = pairs.filter(([l, r]) => l && r);
  const leftKeys = complete.map(([l]) => l);
  const rightKeys = complete.map(([, r]) => r);
  return {
    left_keys: leftKeys, right_keys: rightKeys,
    left_keys_column_ids: idsFor(leftKeys, leftColumns),
    right_keys_column_ids: idsFor(rightKeys, rightColumns),
    left_on: undefined, right_on: undefined,
  };
}

/** Colonne tenute di un lato, risolte per id (null = tutte). */
export function readKeptColumns(config, side, columns) {
  const names = config[`${side}_columns`];
  if (names == null) return null;
  const ids = config[`${side}_columns_column_ids`] || [];
  return names.map((n, i) => resolveName(columns, ids[i], n));
}

/**
 * Patch delle colonne tenute di un lato, con i loro id: tutte spuntate =
 * null (le colonne che arriveranno a monte entrano da sole) e niente id.
 */
export function keptColumnsPatch(side, columns, kept) {
  const names = columns.map(c => c.name);
  const value = names.every(n => kept.includes(n)) ? null : names.filter(n => kept.includes(n));
  return {
    [`${side}_columns`]: value,
    [`${side}_columns_column_ids`]: value == null ? undefined : idsFor(value, columns),
  };
}

// ── Seleziona e rinomina (select_columns e Output) ─────────────────────────
/**
 * Righe della griglia: prima le voci configurate (nel loro ordine), poi le
 * colonne a monte non ancora elencate (tenute, stesso nome: e' quello che
 * fa il backend). Il nome a monte si risolve per id (name_column_id).
 * Le voci che non esistono piu' a monte restano (`missing`): il backend le
 * salta, e tornano valide se la colonna ricompare.
 * `label` vuota = stesso nome della colonna.
 */
export function gridRows(upstream, config) {
  const byName = new Map(upstream.map(c => [c.name, c]));
  const rows = [];
  const seen = new Set();
  for (const item of Array.isArray(config) ? config : []) {
    if (!item || typeof item !== 'object' || !item.name) continue;
    const name = resolveName(upstream, item.name_column_id, item.name);
    if (seen.has(name)) continue;
    seen.add(name);
    rows.push({
      ...item, name, keep: item.keep !== false, label: item.label ?? '',
      type: byName.get(name)?.type || '', missing: !byName.has(name),
      optional: Boolean(byName.get(name)?.optional),
    });
  }
  for (const c of upstream) {
    if (!seen.has(c.name)) rows.push({ name: c.name, keep: true, label: '', type: c.type || '', missing: false, optional: Boolean(c.optional) });
  }
  return rows;
}

/**
 * Configurazione dalle righe: ordine esplicito, label solo se diversa.
 * Ogni voce porta l'id della colonna a monte (name_column_id), anche
 * nell'Output (l'esecuzione risolve gli id sul proprio ingresso). `withIds`
 * false resta per chi scrive solo nomi.
 */
export const gridConfig = (rows, upstream = [], { withIds = true } = {}) => rows.map((r) => {
  // eslint-disable-next-line no-unused-vars
  const { type, missing, optional, display, label, keep, name_column_id: savedId, ...rest } = r;
  const out = { ...rest, keep: keep !== false };
  const id = withIds ? (columnId(upstream.find(c => c.name === r.name)) || savedId) : null;
  if (id) out.name_column_id = id;
  if (label && label !== r.name) out.label = label;
  return out;
});


/**
 * Errore della griglia, o null.
 *  - Pipeline (select_columns, Output): la regola e' quella del backend,
 *    quindi si chiede alla STESSA funzione verificata sui casi generati dal
 *    Python (columnPlans.selectColumnPlan) sulla configurazione che si
 *    scriverebbe. Nessuna regola scritta a parte.
 *  - Estrai (`identifier`): i nomi finiscono in SQL senza virgolette, quindi
 *    minuscolo con lettere, cifre e _. I nomi ripetuti tra tabelle non sono
 *    un errore: l'esecuzione di Estrai li rinomina da sola (_2, _3).
 */
export function gridError(rows, { identifier = false } = {}) {
  if (identifier) {
    const bad = rows.find(r => r.keep && !r.missing && r.label && !/^[a-z_][a-z0-9_]{0,127}$/.test(r.label));
    return bad ? `"${bad.label}": in Estrai i nomi usano solo minuscole, cifre e _ (e non iniziano con una cifra)` : null;
  }
  const names = rows.filter(r => !r.missing).map(r => r.name);
  return selectColumnPlan(names, gridConfig(rows), { skipMissing: true }).error;
}

/** Errore della configurazione SALVATA (stessa funzione del backend), o null. */
export const savedSelectionError = (upstream, config) => (Array.isArray(config) && config.length
  ? selectColumnPlan(upstream.map(c => c.name), config, { skipMissing: true }).error
  : null);

/** snake_case -> "Testo leggibile" (gli acronimi restano maiuscoli). */
export const prettyName = (name) => {
  const words = String(name).split(/_+/).filter(Boolean);
  if (!words.length) return name;
  const out = words.map(w => (w === w.toUpperCase() && /[A-Z]/.test(w) ? w : w.toLowerCase())).join(' ');
  return out[0].toUpperCase() + out.slice(1);
};

/** Toglie i suffissi del join (_L, _R, _dx), anche ripetuti. */
export const stripJoinSuffix = (name) => String(name).replace(/(_(L|R|dx))+$/, '');

// ── Suggerimento Lookup (Case When -> Mappa valori) ────────────────────────
const STRING_TYPES = ['str', 'string', 'text', 'object', 'varchar'];

/**
 * Un Case When si converte in Lookup solo se il risultato e' IDENTICO:
 *  - la colonna e' testo (il Case When confronta come testo, il Lookup per
 *    valore esatto: su numeri, date o booleani divergono);
 *  - solo condizioni "uguale" esplicite (operatore assente = "==" nel
 *    backend; "" invece non trova nulla) su valori non numerici e distinti;
 *  - risultati e default testuali senza virgole (il Case When non accetta
 *    la virgola decimale, il Lookup si'), nessun risultato nullo;
 *  - la colonna risultato non e' la colonna testata (il Case When la
 *    sovrascrive prima di confrontare).
 */
export function lookupFromConditional(config = {}, columns = []) {
  const conds = Array.isArray(config.conditions) ? config.conditions : [];
  if (!config.column || conds.length === 0) return null;
  const col = columns.find(c => c.name === config.column);
  if (!col || !STRING_TYPES.includes(String(col.type || '').toLowerCase())) return null;
  const output = config.output_column || 'result';
  if (output === config.column) return null;
  if (conds.some(c => (c.operator !== undefined && c.operator !== '==')
    || isBlank(c.value) || isPythonFloat(c.value))) return null;
  const values = conds.map(c => String(c.value));
  if (new Set(values).size !== values.length) return null;
  const results = conds.map(c => c.result);
  const def = config.default_value ?? '';
  if (results.some(r => r === null || r === undefined) || [...results, def].some(r => String(r).includes(','))) return null;
  // Risultati numerici: il Case When converte la colonna in numero e le
  // righe senza corrispondenza con default vuoto diventano vuote.
  const numeric = results.every(r => r === '' || isPythonFloat(r)) && (def === '' || isPythonFloat(def));
  const unmatched = def === '' && numeric ? 'null' : 'value';
  return {
    column: config.column,
    column_column_id: config.column_column_id,
    mapping: conds.map(c => ({ from: String(c.value), to: String(c.result) })),
    output_column: output,
    unmatched,
    ...(unmatched === 'value' ? { default: String(def) } : {}),
  };
}

