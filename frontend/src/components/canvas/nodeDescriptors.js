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
import { getCachedTransformRegistry } from '../../utils/transformRegistry';

const count = (v) => (Array.isArray(v) ? v.length : 0);
const visibleColumns = (cols) => (Array.isArray(cols) ? cols.filter(c => c && c.visible !== false).length : 0);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const joinNames = (list, max = 3) => {
  const names = (Array.isArray(list) ? list : []).filter(Boolean).map(String);
  if (names.length <= max) return names.join(', ');
  return `${names.slice(0, max).join(', ')} +${names.length - max}`;
};

const IN = { type: 'target', position: 'left' };
const OUT = { type: 'source', position: 'right' };

// Operazioni a due input (stessa lista di TransformNode).
const MULTI_INPUT_OPS = ['join', 'concat', 'sjoin', 'geo_clip', 'geo_intersection', 'geo_union', 'geo_difference', 'table_diff'];

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
    case 'join': return `${c.how || 'inner'} su ${joinNames(c.left_keys) || '?'}`;
    case 'lookup': return `${c.column || '?'} → ${c.output_column || c.column || '?'}`;
    case 'type_cast': return `${c.column || joinNames(c.columns) || '?'} → ${c.target_type || c.dtype || '?'}`;
    case 'sort': return `ordina per ${joinNames(c.columns || c.by) || '?'}`;
    case 'distinct': return c.columns ? `distinti su ${joinNames(c.columns)}` : 'righe distinte';
    default: {
      const entries = Object.entries(c)
        .filter(([k, v]) => !k.endsWith('_column_id') && !k.endsWith('_column_ids') && !k.endsWith('_refs')
          && v !== null && v !== undefined && v !== '' && typeof v !== 'object');
      return entries.slice(0, 2).map(([k, v]) => `${k}: ${v}`).join(' · ');
    }
  }
}

function transformInfo(data) {
  const op = data?.transform_op;
  const def = getCachedTransformRegistry()?.transforms?.[op];
  return { op, name: def?.name || op || 'Trasformazione', icon: def?.icon || '⚙️' };
}

const D = {
  tableNode: {
    category: 'source', icon: '🗂️', kind: () => 'Tabella',
    title: (d) => d.alias || d.table_name || 'Tabella',
    summary: (d) => {
      const filters = count(d.filters);
      return `${visibleColumns(d.columns)}/${count(d.columns)} colonne${filters ? ` · ${plural(filters, 'filtro', 'filtri')}` : ''}`;
    },
    defaultHandles: () => [IN, OUT],
  },
  featureLayerNode: {
    category: 'source', icon: '🗺️', kind: () => 'Layer ArcGIS',
    title: (d) => d.layer_name || 'Layer',
    summary: (d) => `${plural(count(d.columns), 'campo', 'campi')}${d.where ? ' · filtrato' : ''}`,
    defaultHandles: () => [IN, OUT],
  },
  project_source: {
    category: 'source', icon: '🗂️', kind: () => 'Progetto sorgente',
    title: (d) => d.project_name || d.label || d.name || d.source_project_id || 'Progetto',
    summary: (d) => plural(visibleColumns(d.columns), 'colonna', 'colonne'),
    defaultHandles: () => [OUT],
  },
  file_source: {
    category: 'source', icon: '📄', kind: () => 'File',
    title: (d) => d.file_name || d.fileName || d.label || 'File',
    summary: (d) => plural(visibleColumns(d.columns), 'colonna', 'colonne'),
    defaultHandles: () => [OUT],
  },
  datasourceNode: {
    category: 'source', icon: '🗄️', kind: (d) => (d.mode === 'carica' ? 'Scrittura Data Source' : 'Data Source'),
    title: (d) => d.datasource_name || 'Data Source',
    summary: (d) => d.file_path || d.folder_path || 'percorso da scegliere',
    defaultHandles: () => [{ ...IN, id: 'carica-in' }, { ...OUT, id: 'source-out' }],
  },
  staticValues: {
    category: 'source', icon: '📝', kind: () => 'Valori statici',
    title: (d) => d.label || 'Valori statici',
    summary: (d) => plural(count(d.fields), 'campo', 'campi'),
    defaultHandles: () => [{ ...OUT, id: 'output' }],
  },
  project_input: {
    category: 'input', icon: '⌨', kind: () => 'Input runtime',
    title: (d) => d.label || d.input_key || 'Input',
    summary: (d) => `${d.input_key || '?'} · ${d.input_kind || 'file'}`,
    defaultHandles: (d) => (d.input_kind === 'connection'
      ? []
      : [{ ...OUT, id: (d.input_kind || 'file') === 'file' ? 'output' : 'parameter' }]),
  },
  transform: {
    category: 'step', icon: (d) => transformInfo(d).icon, kind: (d) => transformInfo(d).name,
    title: (d) => transformInfo(d).name,
    summary: (d) => transformSummary(d.transform_op, d.config),
    defaultHandles: (d) => (MULTI_INPUT_OPS.includes(d.transform_op)
      ? [{ ...IN, id: 'input-0' }, { ...IN, id: 'input-1' }, OUT]
      : [IN, OUT]),
  },
  multi_join: {
    category: 'join', icon: '🔗', kind: () => 'Multi-Join',
    title: () => 'Unione',
    summary: (d) => {
      const c = d.config || {};
      return `${c.join_type || d.multi_join_type || 'inner'} su ${joinNames(c.join_keys || d.join_keys) || '?'}`;
    },
    // Almeno 2 ingressi; quelli effettivamente collegati arrivano dagli edge.
    defaultHandles: () => [{ ...IN, id: 'input-0' }, { ...IN, id: 'input-1' }, OUT],
    nextFreeInput: true,
  },
  api_enrich: {
    category: 'step', icon: '🔌', kind: () => 'Arricchimento API',
    title: (d) => d.api_name || d.name || 'API',
    summary: (d) => {
      const params = count(d.parameters);
      const mapped = Object.values(d.param_mapping || {}).filter(Boolean).length;
      return params ? `${mapped}/${params} parametri collegati` : (d.endpoint?.url || d.url || 'API da configurare');
    },
    defaultHandles: () => [{ ...IN, id: 'input' }, { ...OUT, id: 'output' }],
  },
  resultNode: {
    category: 'output', icon: '📁', kind: () => 'Output',
    title: (d) => d.title || 'Output',
    summary: (d) => `${String(d.format || 'csv').toUpperCase()} · ${d.fileName || d.file_name || 'output'}`,
    defaultHandles: () => [{ ...IN, id: 'input' }, { ...OUT, id: 'output' }],
  },
  carica: {
    category: 'load', icon: '💾', kind: () => 'Carica su database',
    title: () => 'Carica DB',
    summary: (d) => (d.dest_type === 'rest'
      ? `REST → ${d.rest_url || '?'}`
      : `→ ${d.schema_name ? `${d.schema_name}.` : ''}${d.table_name || '?'}${d.write_mode ? ` · ${d.write_mode}` : ''}`),
    defaultHandles: () => [IN],
  },
  carica_arcgis: {
    category: 'load', icon: '🌐', kind: () => 'Carica su ArcGIS',
    title: () => 'Carica ArcGIS',
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
  category: 'step', icon: '⚙️', kind: () => 'Nodo',
  title: (d) => d.title || d.label || 'Nodo',
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

/** Titolo mostrato: quello scelto dall'utente (data.title) o quello del tipo. */
export function nodeTitle(type, data = {}) {
  return (data && data.title) || nodeDescriptor(type).title(data || {});
}

export const DESCRIBED_NODE_TYPES = [...Object.keys(D), ...Object.keys(ALIASES)];
