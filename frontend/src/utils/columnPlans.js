/**
 * columnPlans — come escono le colonne da "Seleziona e rinomina" e dal Join.
 *
 * Copia fedele di backend/app/shared/execution/column_selection.py
 * (select_column_plan, join_column_plan): la usano i pannelli del nodo
 * (anteprima "Risultato: N colonne") e la previsione delle colonne a valle
 * (useColumnPropagation). Una sola implementazione lato frontend, cosi'
 * pannello e propagazione non possono divergere tra loro.
 *
 * Differenza voluta: qui non si lancia un'eccezione, si restituisce
 * `error` (il pannello lo mostra, la propagazione lascia le colonne a monte).
 * Regola unica per le colonne sparite a monte: select_columns e Output le
 * saltano (skipMissing), nel backend come qui.
 */
import AGGREGATION_TYPES from './aggregationTypes.json';
import TYPE_FAMILIES from './typeFamilies.json';

const SUFFIX = { left: '_L', right: '_R' };

/**
 * Piano di select_columns / output_columns.
 * @param {string[]} names colonne a monte, in ordine
 * @param {Array<{name, keep?, label?}>} columns configurazione
 * @param {{ skipMissing?: boolean }} opts skipMissing: voci non piu' a monte
 *   ignorate (Output: le colonne possono sparire dopo la configurazione)
 * @returns {{ plan: Array<[string, string]>, error: string|null }}
 */
export function selectColumnPlan(names, columns, { skipMissing = false } = {}) {
  // Istruzione per istruzione come select_column_plan del backend (i casi
  // generati dal Python lo verificano: tests/shared/columnPlansParity).
  const fail = (error) => ({ plan: [], error });
  if (!Array.isArray(columns)) return fail('columns deve essere una lista');
  const isDict = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
  const plan = [];
  const seen = new Set();
  for (const item of columns) {
    if (!isDict(item) || typeof item.name !== 'string' || !item.name.trim()) return fail('Voce di colonna non valida');
    const { name } = item;
    if (skipMissing && !names.includes(name)) continue;
    if (seen.has(name)) return fail(`Colonna duplicata: ${name}`);
    seen.add(name);
    const keep = 'keep' in item ? item.keep : true;
    if (typeof keep !== 'boolean') return fail(`keep non valido per ${name}`);
    let label = 'label' in item ? item.label : null;
    if (label === null || label === undefined || label === '') label = name;
    if (typeof label !== 'string' || (keep && !label.trim())) return fail(`Nome in uscita non valido per ${name}`);
    if (keep) plan.push([name, label]);
  }
  for (const n of names) if (!seen.has(n)) plan.push([n, n]);
  const outputs = plan.map(([, label]) => label);
  const dup = outputs.find((o, i) => outputs.indexOf(o) !== i);
  if (dup !== undefined) return fail(`Nome in uscita ripetuto: ${dup}`);
  const missing = columns.filter(item => !names.includes(item.name)).map(item => item.name);
  if (missing.length && !skipMissing) return fail(`Colonna non trovata: ${missing.join(', ')}`);
  return { plan: plan.filter(([n]) => names.includes(n)), error: null };
}

/**
 * Piano del Join (nuovo percorso: left_columns / right_columns / name_conflict).
 * @returns {{ left: Map<string,string>, right: Map<string,string>, outputs: string[], error: string|null }}
 */
export function joinColumnPlan(left, right, leftKeys, rightKeys, leftColumns, rightColumns, nameConflict) {
  const fail = (error) => ({ left: new Map(), right: new Map(), outputs: [], error });
  const mode = nameConflict == null ? 'suffix' : nameConflict;
  if (!['suffix', 'keep_left', 'keep_right'].includes(mode)) return fail('Gestione dei nomi uguali non valida');
  const sides = [];
  for (const [names, keys, selected] of [[left, leftKeys, leftColumns], [right, rightKeys, rightColumns]]) {
    if (selected != null && (!Array.isArray(selected) || selected.some(n => !names.includes(n)))) {
      return fail('Colonna selezionata non trovata');
    }
    if (keys.some(k => !names.includes(k))) return fail('Colonna chiave non trovata');
    sides.push(names.filter(n => selected == null || selected.includes(n) || keys.includes(n)));
  }
  const sharedKeys = leftKeys.filter(k => rightKeys.includes(k));
  if (sharedKeys.some(k => leftKeys.indexOf(k) !== rightKeys.indexOf(k))) {
    return fail('Chiavi con lo stesso nome associate a coppie diverse: rinominale prima del join');
  }
  const common = new Set(sides[0].filter(n => sides[1].includes(n)));
  const keys = new Set([...leftKeys, ...rightKeys]);
  const maps = [];
  [[sides[0], leftKeys, 'left'], [sides[1], rightKeys, 'right']].forEach(([names, ownKeys, side], idx) => {
    const mapping = new Map();
    for (const name of names) {
      if (ownKeys.includes(name)) mapping.set(name, name);
      else if (keys.has(name)) mapping.set(name, name + SUFFIX[side]);
      else if (mode === 'suffix') mapping.set(name, name + SUFFIX[side]);
      else if (!common.has(name) || (mode === 'keep_left') === (idx === 0)) mapping.set(name, name);
    }
    maps.push(mapping);
  });
  const outputs = [...maps[0].values(), ...[...maps[1].entries()].filter(([k]) => !sharedKeys.includes(k)).map(([, v]) => v)];
  const dup = outputs.find((o, i) => outputs.indexOf(o) !== i);
  if (dup) return fail(`Due colonne in uscita si chiamano "${dup}": rinominale prima del join`);
  return { left: maps[0], right: maps[1], outputs, error: null };
}

// ── Preparazione dei nomi del Join: copia di
// backend/app/shared/execution/join_preparation.py (la STESSA usata da
// esecuzione e inferenza, Blocco A A1). Le colonne in uscita sono triple
// [lato 0/1, posizione nella sorgente, nome finale].

function deduplicateJoinNames(names, keys) {
  const seen = new Map();
  const keySeen = new Map();
  return names.map((name) => {
    if (keys.includes(name)) {
      const n = (keySeen.get(name) || 0) + 1;
      keySeen.set(name, n);
      return n === 1 ? name : `${name}_dup${n}`;
    }
    const n = (seen.has(name) ? seen.get(name) : -1) + 1;
    seen.set(name, n);
    return n === 0 ? name : `${name}_${n}`;
  });
}

function suffixJoinNames(names, keys, suffix) {
  const done = new Map();
  return names.map((name) => {
    if (keys.includes(name)) {
      const n = (done.get(name) || 0) + 1;
      done.set(name, n);
      return n === 1 ? name : `${name}_${suffix}_dup${n}`;
    }
    return `${name}_${suffix}`;
  });
}

/**
 * prepared_join_plan del backend. `maps` (percorso nuovo) e' la coppia di
 * Map nome sorgente -> nome in uscita di joinColumnPlan; senza `maps` e' il
 * percorso di prima (_L/_R).
 * @returns {Array<[number, number, string]>}
 */
export function preparedJoinPlan(left, right, leftKeys, rightKeys, maps = null) {
  const sides = [[left, leftKeys, 'L'], [right, rightKeys, 'R']].map(([names, keys, suffix], side) => {
    const indices = maps == null
      ? names.map((_, i) => i)
      : [...maps[side].keys()].flatMap(selected => names.flatMap((n, i) => (n === selected ? [i] : [])));
    const deduped = deduplicateJoinNames(indices.map(i => names[i]), keys);
    const renamed = maps == null
      ? suffixJoinNames(deduped, keys, suffix)
      : deduped.map(n => (maps[side].has(n) ? maps[side].get(n) : n));
    const unique = new Map();
    indices.forEach((index, k) => { if (!unique.has(renamed[k])) unique.set(renamed[k], index); });
    return unique;
  });
  const common = new Set([...sides[0].keys()].filter(n => sides[1].has(n)));
  const pairs = leftKeys.map((lk, i) => [lk, rightKeys[i]]).filter(([, rk]) => rk !== undefined);
  const sharedKeys = new Set(pairs.filter(([lk, rk]) => lk === rk).map(([lk]) => lk));
  const dropRight = new Set(pairs.filter(([lk, rk]) => lk !== rk && !leftKeys.includes(rk)).map(([, rk]) => rk));
  const result = [...sides[0].entries()].map(([name, index]) => [0, index, name]);
  for (const [name, index] of sides[1].entries()) {
    if (sharedKeys.has(name)) continue;
    const output = common.has(name) ? `${name}_dup` : name;
    if (maps == null && dropRight.has(output)) continue;
    result.push([1, index, output]);
  }
  return result;
}

// Come in Python: liste obbligatorie; i NOMI delle colonne diventano chiavi
// di dizionario, quindi niente liste o oggetti (le chiavi del join si
// confrontano soltanto).
const invalidLists = ([left, right, leftKeys, rightKeys]) => [left, right, leftKeys, rightKeys].some(l => !Array.isArray(l))
  || [left, right].some(l => l.some(v => v !== null && typeof v === 'object'));

/**
 * Colonne in uscita dal Join, nel percorso nuovo (scelta delle colonne e
 * gestione dei nomi uguali).
 * @returns {{ columns: Array<[number,number,string]>, error: string|null }}
 */
export function joinOutputPlan(left, right, leftKeys, rightKeys, leftColumns, rightColumns, nameConflict) {
  const plan = joinColumnPlan(left, right, leftKeys, rightKeys, leftColumns, rightColumns, nameConflict);
  if (plan.error) return { columns: [], error: plan.error };
  return { columns: preparedJoinPlan(left, right, leftKeys, rightKeys, [plan.left, plan.right]), error: null };
}

/** Join nel percorso di prima (_L/_R). */
export function legacyJoinPlan(left, right, leftKeys, rightKeys) {
  if (invalidLists([left, right, leftKeys, rightKeys])) {
    return { columns: [], error: 'Join: colonne o chiavi non valide' };
  }
  return { columns: preparedJoinPlan(left, right, leftKeys, rightKeys), error: null };
}

/** Famiglia di un tipo (copia di schema_column_plans.type_family, stessa tabella typeFamilies.json). */
export function typeFamily(sourceType) {
  const value = String(sourceType ?? '').trim().toLowerCase();
  const prefix = TYPE_FAMILIES.geometry_prefix;
  if (value.startsWith(prefix)) return prefix;
  return TYPE_FAMILIES.aliases[value] ?? value;
}

/**
 * Tipo di una colonna aggregata: la tabella e' GENERATA dal backend eseguendo
 * op_aggregate (backend/app/shared/execution/aggregation_types.json, Blocco A
 * A1); questa e' una copia identica, verificata da un test. Se la famiglia non
 * cambia resta il tipo d'ingresso (sottotipo della geometria compreso).
 * null = la coppia funzione/tipo non e' eseguibile.
 */
export function aggregationOutputType(func, sourceType) {
  const family = typeFamily(sourceType);
  const fn = String(func).toLowerCase();
  const table = AGGREGATION_TYPES[fn];
  // Famiglia sconosciuta alla tabella (json, uuid, ...): regola di prima.
  if (table && !(family in table)) return ['count', 'size', 'nunique'].includes(fn) ? 'int' : sourceType;
  const outcome = table?.[family] ?? 'error';
  if (outcome === 'error') return null;
  return outcome === family ? sourceType : outcome;
}

/** Il join usa il nuovo percorso (stessa regola del backend). */
export const usesJoinColumnPlan = (config = {}) =>
  config.left_columns != null || config.right_columns != null || config.name_conflict != null;

const columnRef = (c) => c?.id || c?.column_id || null;

/**
 * Voci con il nome ATTUALE della colonna: name_column_id vince sul nome
 * (come il backend, config_column_refs): una colonna rinominata a monte
 * resta quella configurata.
 */
export function resolveSelectNames(columns, config) {
  if (!Array.isArray(config)) return config;
  return config.map((item) => {
    if (!item || typeof item !== 'object' || !item.name_column_id) return item;
    const found = columns.find(c => columnRef(c) === item.name_column_id);
    return found ? { ...item, name: found.name } : item;
  });
}

/** Colonne ({name, id?, ...}) dopo select_columns / output_columns. Errore: invariate. */
export function applySelectColumns(columns, config, opts = {}) {
  if (!Array.isArray(config) || config.length === 0) return columns;
  const { plan, error } = selectColumnPlan(columns.map(c => c.name), resolveSelectNames(columns, config), opts);
  if (error) return columns;
  const byName = new Map(columns.map(c => [c.name, c]));
  return plan.map(([name, label]) => (label === name
    ? byName.get(name)
    : { ...byName.get(name), name: label, display_name: label }));
}

const LOAD_TYPES = ['carica', 'carica_arcgis', 'caricaArcgis'];

// Semantica Python usata dalle regole del backend copiate qui.
const pyTruthy = (v) => !(v === null || v === undefined || v === false || v === 0 || v === ''
  || (Array.isArray(v) && v.length === 0) || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0));
const pyStr = (v) => (v === null || v === undefined ? 'None' : v === true ? 'True' : v === false ? 'False' : String(v));
const pyIter = (v) => (typeof v === 'string' ? [...v] : Array.isArray(v) ? v
  : (v && typeof v === 'object' ? Object.keys(v) : (() => { throw new Error('not iterable'); })()));
// node_field: valore piatto non nullo, poi data[key], poi default.
const nodeField = (node, key, def) => {
  if (node?.[key] !== null && node?.[key] !== undefined) return node[key];
  if (node?.data && typeof node.data === 'object' && key in node.data) return node.data[key];
  return def;
};
const AGG_FUNCTIONS = ['sum', 'mean', 'min', 'max', 'std'];

/**
 * Piano del GROUP BY dell'Output: copia di backend
 * shared/execution/result_grouping.py (result_group_plan), verificata sugli
 * stessi casi generati dal Python (tests/shared/columnPlansParity).
 * @returns {{ groups: string[], aggregations: Array<[any, string, any]>, error: string|null }}
 */
export function resultGroupPlan(node, names) {
  try {
    if (!pyTruthy(nodeField(node, 'group_by_enabled', false))) return { groups: [], aggregations: [], error: null };
    const groupsRaw = nodeField(node, 'group_by_columns', []);
    let groups = pyIter(pyTruthy(groupsRaw) ? groupsRaw : []);
    let aggregations = [];
    const aggsRaw = nodeField(node, 'aggregations', []);
    for (const agg of pyIter(pyTruthy(aggsRaw) ? aggsRaw : [])) {
      if (!agg || typeof agg !== 'object' || Array.isArray(agg)) throw new Error('aggregazione non valida');
      const col = agg.column ?? null;
      let func = pyStr('function' in agg ? agg.function : 'count').toLowerCase();
      if (func === 'avg') func = 'mean';
      aggregations.push([col, func, pyTruthy(agg.alias) ? agg.alias : `${pyStr(col)}_${func}`]);
    }
    if (groups.length === 0 && aggregations.length === 0) return { groups: [], aggregations: [], error: null };
    groups = groups.filter(n => names.includes(n));
    if (groups.length === 0) return { groups: [], aggregations: [], error: 'GROUP BY: nessuna delle colonne configurate esiste' };
    aggregations = aggregations.filter(([col, func]) => func === 'count' || (names.includes(col) && AGG_FUNCTIONS.includes(func)));
    if (aggregations.length === 0) return { groups: [], aggregations: [], error: 'GROUP BY: nessuna aggregazione valida' };
    return { groups, aggregations, error: null };
  } catch (e) {
    return { groups: [], aggregations: [], error: String(e.message || e) };
  }
}

/**
 * Colonne che escono davvero dall'Output (Manipola/Arricchisci): prima
 * "Colonne in uscita", poi GROUP BY se attivo (stessi nomi del backend:
 * colonne di raggruppamento, poi alias o `${colonna}_${funzione}`).
 */
export function resultOutputColumns(available, data = {}) {
  const selected = applySelectColumns(available, data.output_columns, { skipMissing: true });
  const plan = resultGroupPlan(data, selected.map(c => c.name));
  // Senza raggruppamento valido: le colonne selezionate (con un GROUP BY
  // sbagliato l'esecuzione si ferma con l'errore, che Valida mostra).
  if (plan.error || plan.groups.length === 0) return selected;
  const out = plan.groups.map(n => selected.find(c => c.name === n));
  for (const [col, fn, alias] of plan.aggregations) {
    const source = selected.find(c => c.name === col);
    // Coppia non eseguibile: Valida la segnala; qui solo un tipo da mostrare.
    out.push({ name: String(alias), type: aggregationOutputType(fn, source?.type || 'real') || 'str' });
  }
  return out;
}

/**
 * Scrive availableColumns sull'Output (colonne a monte) e sui nodi Carica
 * (colonne che escono davvero dall'Output, resultOutputColumns). Restituisce
 * `nodes` invariato se non cambia nulla (aggiornamento derivato).
 */
export function withAvailableColumns(nodes, available) {
  const result = nodes.find(n => n.type === 'result');
  const loaded = resultOutputColumns(available, result?.data || {});
  let changed = false;
  const next = nodes.map((n) => {
    const cols = n.type === 'result' ? available : LOAD_TYPES.includes(n.type) ? loaded : null;
    if (!cols || JSON.stringify(n.data?.availableColumns || []) === JSON.stringify(cols)) return n;
    changed = true;
    return { ...n, data: { ...n.data, availableColumns: cols } };
  });
  return changed ? next : nodes;
}

/**
 * Quando cambiano le "Colonne in uscita", le altre impostazioni dell'Output
 * che nominano colonne (GROUP BY, aggregazioni, ORDER BY, HAVING,
 * geometria) seguono: rinominate col nuovo nome, tolte se la colonna non
 * esce piu'. Restituisce la patch da scrivere insieme a output_columns.
 */
export function outputReferencesPatch(data, upstreamColumns, nextOutputColumns) {
  const upstreamNames = upstreamColumns.map(c => c.name);
  const outNames = (config) => {
    const resolved = resolveSelectNames(upstreamColumns, Array.isArray(config) ? config : []);
    const { plan } = selectColumnPlan(upstreamNames, resolved, { skipMissing: true });
    return new Map(plan.map(([src, out]) => [src, out]));
  };
  const before = outNames(data.output_columns);
  const after = outNames(nextOutputColumns);
  const rename = new Map();
  for (const [src, oldOut] of before) rename.set(oldOut, after.has(src) ? after.get(src) : null);
  const map = (name) => (rename.has(name) ? rename.get(name) : name);
  const patch = {};
  const touch = (key, value) => { if (JSON.stringify(value) !== JSON.stringify(data[key])) patch[key] = value; };
  if (Array.isArray(data.group_by_columns)) touch('group_by_columns', data.group_by_columns.map(map).filter(Boolean));
  // Voci con una colonna: rinominate, tolte se la colonna non esce piu'.
  const byColumn = (list) => list
    .map(x => (x && x.column ? { ...x, column: map(x.column) } : x))
    .filter(x => !(x && x.column === null));
  for (const key of ['aggregations', 'order_by_columns', 'having_conditions']) {
    if (Array.isArray(data[key])) touch(key, byColumn(data[key]));
  }
  if (data.geomColumn) touch('geomColumn', map(data.geomColumn));
  return patch;
}
