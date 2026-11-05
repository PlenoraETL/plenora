/**
 * responseConfigEngine.js — Pure helpers per il response-config builder
 * Contiene solo funzioni pure (no React, no DOM, no API, no global
 * state) per:
 *  - analizzare la struttura JSON di una risposta sample;
 *  - raccogliere path e campi scalari per auto-detect;
 *  - simulare l'estrazione delle righe (preview locale) a partire da
 *    una `config` con root_path / iterate_on / mappings / transforms;
 *  - applicare i transform supportati (kelvin↔celsius / round /
 *    multiply / divide / add / subtract / uppercase / lowercase /
 *    prefix / suffix).
 *
 * Tutti gli helper sono esportati come named exports per consumo
 * esplicito da `ResponseConfigBuilder.jsx`.
 */

/**
 * Analizza la struttura di un oggetto JSON.
 * Ritorna un `AbstractJsonNode`:
 *   { name, type: 'object'|'array'|'integer'|'number'|'string'|'boolean'|'null',
 *     fullPath, count?, children?, sample? }
 *
 * Per array di oggetti usa `[*]` come placeholder nel path; per
 * array di primitivi enumera fino ai primi 10 indici (`[0]`..`[9]`).
 */
export function analyzeJsonStructure(data, name, depth, parentPath = '') {
  const fullPath = parentPath ? `${parentPath}.${name}` : name;

  if (data === null || data === undefined) {
    return { name, type: 'null', fullPath, sample: null };
  }

  if (Array.isArray(data)) {
    const sample = data[0];

    // Se l'array contiene oggetti, mostra i campi del primo elemento (es. features[])
    if (sample && typeof sample === 'object' && !Array.isArray(sample)) {
      return {
        name,
        type: 'array',
        fullPath,
        count: data.length,
        children: Object.entries(sample).map(([key, value]) =>
          analyzeJsonStructure(value, key, depth + 1, `${fullPath}[*]`)
        )
      };
    }

    // Array di primitivi (es. latlng: [42.83, 12.83]) — mostra tutti gli indici
    // Creiamo i nodi direttamente per evitare il punto spurio prima di [idx]
    // (la ricorsione produrrebbe "latlng.[0]" invece di "latlng[0]")
    const children = data.slice(0, 10).map((item, idx) => {
      let t = typeof item;
      if (t === 'number') t = Number.isInteger(item) ? 'integer' : 'number';
      return { name: `[${idx}]`, type: t, fullPath: `${fullPath}[${idx}]`, sample: item };
    });

    return { name, type: 'array', fullPath, count: data.length, children };
  }

  if (typeof data === 'object') {
    return {
      name,
      type: 'object',
      fullPath,
      children: Object.entries(data).map(([key, value]) =>
        analyzeJsonStructure(value, key, depth + 1, fullPath)
      )
    };
  }

  // Scalare
  let type = typeof data;
  if (type === 'number') {
    type = Number.isInteger(data) ? 'integer' : 'number';
  }

  return { name, type, fullPath, sample: data };
}

/**
 * Raccoglie tutti i path da espandere fino a `maxDepth`.
 * Mutates `paths` (Set).
 */
export function collectPaths(node, paths, depth, maxDepth) {
  if (depth >= maxDepth) return;

  if (node.children) {
    node.children.forEach(child => {
      const childPath = node.fullPath ? `${node.fullPath}.${child.name}` : child.name;
      paths.add(childPath);
      collectPaths(child, paths, depth + 1, maxDepth);
    });
  }
}

/**
 * Raccoglie campi scalari per auto-detect dei mapping.
 * Tiene conto degli alias `iterate_on` per costruire il path
 * relativo al contesto iterato.
 * Mutates `mappings` (array).
 */
export function collectScalarFields(node, contextPath, mappings, iterateOn) {
  // Costruisci il path considerando gli alias iterate_on
  let fullPath = contextPath ? `${contextPath}.${node.name}` : node.name;

  // Se è un iterato, usa l'alias
  const iterateMatch = iterateOn.find(i => i.path === node.fullPath);
  if (iterateMatch) {
    fullPath = iterateMatch.as;
  }

  if (node.type !== 'array' && node.type !== 'object') {
    // È uno scalare
    mappings.push({
      column: node.name,
      path: fullPath || node.name,
      type: node.type
    });
  }

  if (node.children) {
    node.children.forEach(child => {
      collectScalarFields(child, fullPath, mappings, iterateOn);
    });
  }
}

/**
 * Estrae righe dalla configurazione (simulazione locale per
 * la preview pre-save).
 * Limite hard-coded a 100 righe quando non ci sono iterate_on.
 */
export function extractRowsFromConfig(data, config) {
  const { root_path, iterate_on, mappings, transforms } = config;

  // Naviga al root path
  let rootData = data;
  if (root_path) {
    for (const part of root_path.split('.')) {
      if (rootData && typeof rootData === 'object') {
        rootData = rootData[part];
      }
    }
  }

  if (!rootData) return [];

  // Se non ci sono iterate, estrai direttamente
  if (!iterate_on || iterate_on.length === 0) {
    const items = Array.isArray(rootData) ? rootData : [rootData];
    // P11 (Fase 5 T7): senza iterazione i percorsi si leggono dall'elemento
    // (prima si leggevano da un contesto vuoto: tutte le colonne null) e le
    // trasformazioni si applicano come nel ramo con iterazione.
    const direct = items.slice(0, 100).map(item => extractRow(item, mappings, {}));
    applyTransforms(direct, transforms);
    return direct;
  }

  // Itera ricorsivamente
  const rows = [];
  recursiveIterate(rootData, iterate_on, 0, {}, rows, mappings);

  // Applica trasformazioni
  applyTransforms(rows, transforms);

  return rows;
}

function recursiveIterate(data, iterateOn, level, context, rows, mappings) {
  if (level >= iterateOn.length) {
    rows.push(extractRow(null, mappings, context));
    return;
  }

  const { path, as } = iterateOn[level];
  let items = path ? navigatePath(data, path) : data;

  if (!Array.isArray(items)) {
    items = items ? [items] : [];
  }

  for (const item of items) {
    const newContext = { ...context, [as]: item };
    recursiveIterate(item, iterateOn, level + 1, newContext, rows, mappings);
  }
}

function extractRow(item, mappings, context) {
  const row = {};

  for (const mapping of mappings) {
    const { column, path } = mapping;

    // Dal contesto dell'iterazione (`as`) se il primo segmento lo nomina,
    // altrimenti dall'elemento corrente.
    const parts = path.split('.');
    const fromContext = Object.prototype.hasOwnProperty.call(context, parts[0]);
    let value = fromContext ? context[parts[0]] : item?.[parts[0]];

    for (let i = 1; i < parts.length && value !== undefined; i++) {
      value = value?.[parts[i]];
    }

    row[column] = value ?? null;
  }

  return row;
}

function navigatePath(data, path) {
  if (!path) return data;

  let current = data;
  for (const part of path.split('.')) {
    if (current === null || current === undefined) return null;
    current = current[part];
  }
  return current;
}

/**
 * Applica trasformazioni in-place sulle righe.
 * Operazioni supportate: none / kelvin_to_celsius / celsius_to_kelvin
 * / round / multiply / divide / add / subtract / uppercase /
 * lowercase / prefix / suffix.
 */
function applyTransforms(rows, transforms) {
  if (!transforms || transforms.length === 0) return;

  for (const row of rows) {
    for (const t of transforms) {
      if (t.operation === 'none') continue;

      const sourceValue = row[t.source];
      if (sourceValue === null || sourceValue === undefined) {
        row[t.column] = null;
        continue;
      }

      switch (t.operation) {
        case 'kelvin_to_celsius':
          row[t.column] = Math.round((parseFloat(sourceValue) - 273.15) * 100) / 100;
          break;
        case 'celsius_to_kelvin':
          row[t.column] = parseFloat(sourceValue) + 273.15;
          break;
        case 'round':
          row[t.column] = Math.round(parseFloat(sourceValue) * Math.pow(10, parseInt(t.value) || 0)) / Math.pow(10, parseInt(t.value) || 0);
          break;
        case 'multiply':
          row[t.column] = parseFloat(sourceValue) * parseFloat(t.value);
          break;
        case 'divide':
          row[t.column] = parseFloat(sourceValue) / parseFloat(t.value);
          break;
        case 'add':
          row[t.column] = parseFloat(sourceValue) + parseFloat(t.value);
          break;
        case 'subtract':
          row[t.column] = parseFloat(sourceValue) - parseFloat(t.value);
          break;
        case 'uppercase':
          row[t.column] = String(sourceValue).toUpperCase();
          break;
        case 'lowercase':
          row[t.column] = String(sourceValue).toLowerCase();
          break;
        case 'prefix':
          row[t.column] = `${t.value}${sourceValue}`;
          break;
        case 'suffix':
          row[t.column] = `${sourceValue}${t.value}`;
          break;
        default:
          row[t.column] = sourceValue;
      }
    }
  }
}
