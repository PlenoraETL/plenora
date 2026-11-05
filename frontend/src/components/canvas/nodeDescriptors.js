/**
 * nodeDescriptors.js — come si presenta ogni tipo di nodo nella nuova
 * interfaccia dei canvas (Fase 4, M1): icona, categoria, titolo, riassunto
 * leggibile e handle standard.
 *
 * Una sola tabella per i tre editor (Estrai, Manipola, Arricchisci): i
 * tipi coincidono con le chiavi di `nodeTypes` di React Flow (alias
 * compresi). Il form completo del nodo NON sta qui: e' il componente
 * esistente, mostrato nel pannello laterale (NodeInspector).
 *
 * Handle: `defaultHandles` elenca quelli che il tipo espone sempre, con
 * gli STESSI id dei componenti completi. Il nodo compatto aggiunge in piu'
 * ogni handle gia' usato da un edge collegato (es. `parameter:<id>` dei
 * binding degli input runtime): nessun collegamento salvato resta orfano.
 */
import { getCachedTransformRegistry, isMultiInputOp } from '../../utils/transformRegistry';
import { KNOWN_OPERATION_NAMES } from '../../utils/operationNames';
import { displayIcon } from '../../utils/displayIcon';
import { TERMS, applyTransformTerms, inputKindLabel, term } from '../../utils/glossary';
import { projectInputHandle } from '../../utils/runtimeInputKinds';

export { displayIcon };

const count = (v) => (Array.isArray(v) ? v.length : 0);
const visibleColumns = (cols) => (Array.isArray(cols) ? cols.filter(c => c && c.visible !== false).length : 0);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const joinNames = (list, max = 3) => {
  const names = (Array.isArray(list) ? list : []).filter(Boolean).map(String);
  if (names.length <= max) return names.join(', ');
  return `${names.slice(0, max).join(', ')} +${names.length - max}`;
};

// Il tipo di join come clausola SQL ("LEFT JOIN"), non come chiave ("left").
const sqlJoin = (how) => `${String(how || 'inner').toUpperCase()} JOIN`;

const IN = { type: 'target', position: 'left' };
const OUT = { type: 'source', position: 'right' };


const OPERATOR_LABELS = {
  '==': '=', '!=': '≠', '>': '>', '>=': '≥', '<': '<', '<=': '≤',
  contains: 'contiene', startswith: 'inizia con', endswith: 'finisce con',
  isnull: 'è vuoto', notnull: 'non è vuoto', in: 'è tra', not_in: 'non è tra',
};

function conditionText(c) {
  if (!c || !c.column) return null;
  const op = OPERATOR_LABELS[c.operator] || c.operator || '=';
  if (c.operator === 'isnull' || c.operator === 'notnull') return `${c.column} ${op}`;
  const value = Array.isArray(c.value) ? c.value.join(', ') : (c.value ?? '');
  return `${c.column} ${op} ${value}`.trim();
}

/** Riassunto leggibile di un'operazione Manipola dalla sua config. */
export function transformSummary(op, config = {}) {
  const c = config || {};
  switch (op) {
    case 'filter': {
      const conds = Array.isArray(c.conditions) ? c.conditions : [c];
      const texts = conds.map(conditionText).filter(Boolean);
      return texts.length ? texts.join(' e ') : 'nessuna condizione';
    }
    case 'formula': {
      const items = Array.isArray(c.items) ? c.items : [c];
      const names = items.map(i => i?.new_column).filter(Boolean);
      return names.length ? `+ ${joinNames(names)}` : 'formula da completare';
    }
    case 'rename': return plural(count(c.renames), 'rinomina', 'rinomine');
    case 'drop_columns': return `− ${plural(count(c.columns), 'colonna', 'colonne')}`;
    case 'select_columns': {
      const cols = Array.isArray(c.columns) ? c.columns : [];
      const dropped = cols.filter(x => x && x.keep === false).length;
      return `${plural(cols.length - dropped, 'colonna tenuta', 'colonne tenute')}${dropped ? ` · ${dropped} scartate` : ''}`;
    }
    case 'aggregate': return `per ${joinNames(c.group_by || c.by) || '?'}`;
    case 'join': return `${sqlJoin(c.how)} su ${joinNames(c.left_keys) || '?'}`;
    case 'lookup': return `${c.column || '?'} → ${c.output_column || c.column || '?'}`;
    case 'type_cast': return `${c.column || joinNames(c.columns) || '?'} → ${c.target_type || c.dtype || '?'}`;
    case 'sort': return `ordina per ${joinNames(c.columns || c.by) || '?'}`;
    case 'distinct': return c.columns ? `distinti su ${joinNames(c.columns)}` : 'righe distinte';
    default: return fallbackSummary(op, c);
  }
}

/*
 * Riepilogo di riserva (operazioni senza un caso dedicato): i primi due
 * parametri valorizzati con l'ETICHETTA del registry e, per le scelte,
 * l'etichetta dell'opzione. Mai le chiavi tecniche della config
 * ("how: inner"); un parametro che il registry non descrive non si mostra.
 */
function fallbackSummary(op, config) {
  const params = getCachedTransformRegistry()?.transforms?.[op]?.parameters || [];
  const parts = [];
  for (const p of params) {
    const v = config[p?.id];
    if (v === null || v === undefined || v === '' || typeof v === 'object') continue;
    const option = (Array.isArray(p.options) ? p.options : []).find(o => o && typeof o === 'object' && o.value === v);
    parts.push(`${p.label || p.id}: ${option?.label || v}`);
    if (parts.length === 2) break;
  }
  return parts.join(' · ');
}

function transformInfo(data) {
  const op = data?.transform_op;
  const def = getCachedTransformRegistry()?.transforms?.[op];
  return { op, name: def?.name || op || term('transform').name, icon: displayIcon(def?.icon) };
}

// Nome e icona di un tipo dal glossario (regola 10 del Blocco A).
const named = (key) => ({ icon: term(key).icon, kind: () => term(key).name });

const D = {
  tableNode: {
    category: 'source', ...named('table'),
    title: (d) => d.alias || d.table_name || term('table').name,
    summary: (d) => {
      const filters = count(d.filters);
      return `${visibleColumns(d.columns)}/${count(d.columns)} colonne${filters ? ` · ${plural(filters, 'filtro', 'filtri')}` : ''}`;
    },
    defaultHandles: () => [IN, OUT],
  },
  featureLayerNode: {
    category: 'source', ...named('feature_layer'),
    title: (d) => d.layer_name || term('feature_layer').name,
    summary: (d) => `${plural(count(d.columns), 'campo', 'campi')}${d.where ? ' · filtrato' : ''}`,
    defaultHandles: () => [IN, OUT],
  },
  project_source: {
    category: 'source', ...named('project_source'),
    title: (d) => d.project_name || d.label || d.name || d.source_project_id || term('project_source').name,
    summary: (d) => plural(visibleColumns(d.columns), 'colonna', 'colonne'),
    defaultHandles: () => [OUT],
  },
  file_source: {
    category: 'source', ...named('file_source'),
    title: (d) => d.file_name || d.fileName || d.label || term('file_source').name,
    summary: (d) => plural(visibleColumns(d.columns), 'colonna', 'colonne'),
    defaultHandles: () => [OUT],
  },
  datasourceNode: {
    category: 'source', icon: term('datasource').icon,
    kind: (d) => term(d.mode === 'carica' ? 'datasource_write' : 'datasource').name,
    title: (d) => d.datasource_name || term('datasource').name,
    summary: (d) => d.file_path || d.folder_path || 'percorso da scegliere',
    defaultHandles: () => [{ ...IN, id: 'carica-in' }, { ...OUT, id: 'source-out' }],
  },
  staticValues: {
    // `label` dei Valori statici non e' testo dell'utente (nessun campo lo
    // modifica): e' il vecchio nome scritto alla creazione.
    category: 'source', ...named('static_values'),
    title: () => term('static_values').name,
    summary: (d) => plural(count(d.fields), 'campo', 'campi'),
    defaultHandles: () => [{ ...OUT, id: 'output' }],
  },
  project_input: {
    category: 'input', ...named('runtime_input'),
    title: (d) => d.label || d.input_key || term('runtime_input').name,
    summary: (d) => `${d.input_key || '?'} · ${inputKindLabel(d.input_kind)}`,
    defaultHandles: (d) => (projectInputHandle(d) ? [{ ...OUT, id: projectInputHandle(d) }] : []),
  },
  transform: {
    category: 'step', icon: (d) => transformInfo(d).icon, kind: (d) => transformInfo(d).name,
    title: (d) => transformInfo(d).name,
    summary: (d) => transformSummary(d.transform_op, d.config),
    defaultHandles: (d) => (isMultiInputOp(d.transform_op)
      ? [{ ...IN, id: 'input-0' }, { ...IN, id: 'input-1' }, OUT]
      : [IN, OUT]),
  },
  multi_join: {
    category: 'join', ...named('multi_join'),
    title: () => term('multi_join').name,
    summary: (d) => {
      const c = d.config || {};
      return `${sqlJoin(c.join_type || d.multi_join_type)} su ${joinNames(c.join_keys || d.join_keys) || '?'}`;
    },
    // Almeno 2 ingressi; quelli effettivamente collegati arrivano dagli edge.
    defaultHandles: () => [{ ...IN, id: 'input-0' }, { ...IN, id: 'input-1' }, OUT],
    nextFreeInput: true,
  },
  api_enrich: {
    category: 'step', ...named('api_enrich'),
    title: (d) => d.api_name || d.name || term('api_enrich').name,
    summary: (d) => {
      const params = count(d.parameters);
      const mapped = Object.values(d.param_mapping || {}).filter(Boolean).length;
      return params ? `${mapped}/${params} parametri collegati` : (d.endpoint?.url || d.url || 'API da configurare');
    },
    defaultHandles: () => [{ ...IN, id: 'input' }, { ...OUT, id: 'output' }],
  },
  resultNode: {
    category: 'output', ...named('output'),
    title: () => term('output').name,
    summary: (d) => `${String(d.format || 'csv').toUpperCase()} · ${d.fileName || d.file_name || 'nome del file da scegliere'}`,
    defaultHandles: () => [{ ...IN, id: 'input' }, { ...OUT, id: 'output' }],
  },
  carica: {
    category: 'load', ...named('load_db'),
    title: () => term('load_db').name,
    summary: (d) => (d.dest_type === 'rest'
      ? `REST → ${d.rest_url || '?'}`
      : `→ ${d.schema_name ? `${d.schema_name}.` : ''}${d.table_name || '?'}${d.write_mode ? ` · ${d.write_mode}` : ''}`),
    defaultHandles: () => [IN],
  },
  carica_arcgis: {
    category: 'load', ...named('load_arcgis'),
    title: () => term('load_arcgis').name,
    summary: (d) => `${d.arcgis_operation || 'add'} · ${d.arcgis_new_layer_name || d.arcgis_layer_url || '?'}`,
    defaultHandles: () => [IN],
  },
};

const ALIASES = {
  result: 'resultNode',
  caricaArcgis: 'carica_arcgis',
  datasource_node: 'datasourceNode',
  projectSource: 'project_source',
  fileSource: 'file_source',
};

const FALLBACK = {
  category: 'step', ...named('transform'),
  title: (d) => d.label || term('transform').name,
  summary: () => '',
  defaultHandles: () => [IN, OUT],
};

/** True se il tipo ha un descrittore dedicato (non quello generico). */
export function hasNodeDescriptor(type) {
  return Boolean(D[ALIASES[type] || type]);
}

export function nodeDescriptor(type) {
  return D[ALIASES[type] || type] || FALLBACK;
}

/** Icona del nodo, sempre un glifo mostrabile. */
export function nodeIcon(type, data = {}) {
  const d = nodeDescriptor(type);
  return displayIcon(typeof d.icon === 'function' ? d.icon(data || {}) : d.icon);
}

/*
 * Titoli (regola 7 del Blocco A): `data.title` contiene SOLO testo scritto
 * dall'utente. Il titolo derivato (dal tipo e dai metadati del nodo) e' il
 * segnaposto del campo titolo e segue da solo i cambi di operazione, le
 * conversioni e i metadati. Tutti i lettori e tutti gli scrittori passano
 * da queste funzioni.
 *
 * `data.branch_of`: metadato di Collega e Duplica ramo (nome del ramo a cui
 * il passo si riferisce), mostrato come suffisso del titolo derivato.
 */

/** Titolo del tipo senza `data.title` e senza suffisso del ramo. */
function baseTitle(type, data) {
  return nodeDescriptor(type).title({ ...(data || {}), title: undefined });
}

/** Titolo derivato: il segnaposto del campo titolo. */
export function derivedTitle(type, data = {}) {
  const d = data || {};
  const base = baseTitle(type, d);
  return d.branch_of ? `${base} · ${d.branch_of}` : base;
}

// Titoli scritti dall'utente da qui in avanti: `data.title_source === 'user'`
// (lo mette titlePatch) e il testo vale COSI' COM'E', anche se coincide con
// il nome di un'operazione o con un nome vecchio.
//
// Titoli SENZA marcatore (salvati dalle versioni precedenti, quando il campo
// veniva precompilato e salvato al blur): un testo uguale a un titolo che il
// sistema generava e' congelato, non dell'utente, e non nasconde il derivato.
// Generati erano: il derivato attuale, i nomi vecchi del glossario
// (`formerNames`), per i passi il nome di qualsiasi operazione (glossario o
// catalogo), per le tabelle il loro nome, e i titoli di Collega ("Unisci X",
// "Accoda X") e Duplica ramo ("<titolo generato> · X").
const FORMER_NAMES = new Set(Object.values(TERMS).flatMap(t => t.formerNames || []));
const GENERATED_OPERATION_NAMES = new Set(Object.entries(KNOWN_OPERATION_NAMES).flatMap(([op, names]) => {
  const [name, catalogName] = names;
  const termed = applyTransformTerms(op, { name, catalog_name: catalogName });
  return [...names, termed?.name, termed?.catalog_name].filter(Boolean);
}));
const LEGACY_BRANCH_TITLE = /^(Unisci|Accoda) \S/;

function isGeneratedName(type, data, text) {
  if (text === derivedTitle(type, data) || text === baseTitle(type, data)) return true;
  if (FORMER_NAMES.has(text)) return true;
  const d = data || {};
  if (text === d.table_name || (d.schema_name && text === `${d.schema_name}.${d.table_name}`)) return true;
  if (nodeDescriptor(type) !== D.transform) return false;
  // Nomi di operazione dalla tabella statica (KNOWN_OPERATION_NAMES, uguale
  // al registry del backend) e i loro nomi del glossario: la risposta non
  // cambia col caricamento del catalogo (Fase 5, T6, R9-U N7).
  return GENERATED_OPERATION_NAMES.has(text);
}

function isLegacyGeneratedTitle(type, data, text) {
  if (isGeneratedName(type, data, text)) return true;
  if (LEGACY_BRANCH_TITLE.test(text)) return true;
  const dot = text.lastIndexOf(' · ');
  return dot > 0 && isGeneratedName(type, data, text.slice(0, dot));
}

/** Testo scelto dall'utente; '' se manca o e' un titolo generato (vecchio). */
export function userTitle(type, data = {}) {
  const text = typeof data?.title === 'string' ? data.title.trim() : '';
  if (!text) return '';
  if (data.title_source === 'user') return text;
  return isLegacyGeneratedTitle(type, data, text) ? '' : text;
}

/** Titolo mostrato: quello scelto dall'utente o quello derivato. */
export function nodeTitle(type, data = {}) {
  return userTitle(type, data) || derivedTitle(type, data);
}

/**
 * Scrittura del titolo dal campo (pannello o doppio clic sul nodo).
 * Ritorna la patch di `data` o null se non c'e' niente da scrivere:
 *  - testo uguale al titolo dell'utente attuale: nessuna scrittura (fuoco
 *    e uscita senza modifiche non rendono il canvas "non salvato");
 *  - testo vuoto o uguale al derivato: si torna al derivato (title tolto);
 *  - altrimenti il testo diventa il titolo dell'utente (title_source:
 *    'user': da quel momento vale cosi' com'e').
 */
export function titlePatch(type, data, text) {
  const next = String(text ?? '').trim();
  const current = userTitle(type, data);
  if (next === current) return null;
  if (!next || next === derivedTitle(type, data)) {
    return current ? { title: undefined, title_source: undefined } : null;
  }
  return { title: next, title_source: 'user' };
}

export const DESCRIBED_NODE_TYPES = [...Object.keys(D), ...Object.keys(ALIASES)];
