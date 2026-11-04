/**
 * columnPlans — come escono le colonne da "Seleziona e rinomina" e dal Join.
 *
 * Copia fedele di backend/app/shared/execution/column_selection.py
 * (select_column_plan, join_column_plan). Dopo la F2 del Blocco A (regola 4)
 * il frontend NON prevede piu' le colonne a valle: quelle vengono dal server
 * (utils/columnSource). Qui restano solo le regole che i pannelli applicano
 * alla configurazione che l'utente sta scrivendo, prima di salvarla:
 * l'errore della griglia "Seleziona e rinomina" e dell'Output, l'anteprima
 * "Risultato: N colonne" del Join, le colonne del GROUP BY dopo "Colonne in
 * uscita". I casi generati dal Python le verificano
 * (tests/shared/columnPlansParity).
 *
 * Differenza voluta: qui non si lancia un'eccezione, si restituisce
 * `error` (il pannello lo mostra). Regola unica per le colonne sparite a
 * monte: select_columns e Output le saltano (skipMissing), nel backend come qui.
 */

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
