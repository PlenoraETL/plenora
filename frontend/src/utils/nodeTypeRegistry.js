import { nd } from './nodeHelpers';
import { REST_AUTH_FIELD } from './restSecrets';
import { MISSING_JOIN_TYPE } from './joinTypes';

// Backend names are canonical; reactFlowType preserves the editors' render keys.
// Only these data fields are persisted. Runtime data is deliberately absent.
const define = (type, aliases, reactFlowType, defaults) => ({
  type, aliases, reactFlowType,
  fields: Object.fromEntries(Object.entries(defaults).map(([name, value]) => [
    name, { default: value, persisted: true },
  ])),
});

export const NODE_TYPES = {
  source: define('source', ['tableNode'], 'tableNode', {
    alias: null,
    table_name: undefined,
    columns: [],
    filters: [],
    calculated_columns: [],
    connection_id: null,
  }),
  feature_source: define('feature_source', ['featureLayerNode'], 'featureLayerNode', {
    columns: [],
    layer_name: 'Layer',
    layer_url: '',
    connection_id: '',
    connection_name: '',
    service_name: '',
    geometry_type: '',
    feature_count: null,
    where: '',
    relationships: [],
  }),
  result: define('result', ['resultNode'], 'resultNode', {
    fileName: 'output',
    format: 'csv',
    distinct: false,
    limit: null,
    geomColumn: null,
    group_by_enabled: false,
    group_by_columns: [],
    aggregations: [],
    order_by_enabled: false,
    order_by_columns: [],
    having_enabled: false,
    having_conditions: [],
  }),
  carica: define('carica', [], 'carica', {
    connection_id: '',
    dest_type: 'db',
    schema_name: '',
    table_name: '',
    write_mode: 'create',
    column_mappings: [],
    match_columns: [],
    batch_size: 1000,
    transaction_mode: 'all_or_nothing',
    create_spatial_index: true,
    conflict_policy: 'abort',
    rest_url: '',
    rest_method: 'POST',
    rest_headers: [],
    // Credenziali REST: riferimento al connettore API, mai il segreto (R10.8).
    [REST_AUTH_FIELD]: '',
    rest_body_mapping: [],
    rest_batch_mode: 'batch',
    rest_batch_size: 1000,
    rest_timeout_seconds: 30,
    rest_success_statuses: [200, 201, 202, 204],
    create_unique_index: false,
    unique_key_column: '',
    update_columns: [],
    if_table_exists: 'error',
    allow_unsafe_append: false,
  }),
  carica_arcgis: define('carica_arcgis', ['caricaArcgis'], 'caricaArcgis', {
    arcgis_connection_id: '',
    arcgis_service_url: '',
    arcgis_layer_url: '',
    arcgis_operation: 'add',
    arcgis_folder_id: '',
    arcgis_match_column: '',
    arcgis_new_service_name: '',
    arcgis_new_layer_name: 'Layer 0',
    source_srid: null,
    column_mappings: [],
    dest_type: 'arcgis',
  }),
  staticValues: define('staticValues', [], 'staticValues', {
    label: 'Valori Statici',
    fields: [],
  }),
  comment: define('comment', [], 'comment', {
    width: 250,
    height: 150,
    level: '',
    label: '',
    title: '',
    color: '#fbbf24',
    arrowStyle: null,
  }),
  datasource_node: define('datasource_node', ['datasourceNode'], 'datasourceNode', {
    mode: 'source',
    datasource_id: '',
    datasource_name: '',
    file_mode: 'fixed',
    file_path: '',
    folder_path: '',
    dynamic_rule: 'latest',
    pattern: '',
    format: '',
    columns: [],
    parse_options: {},
    dest_folder: '',
    dest_filename: '',
    dest_format: 'csv',
    file_offset: 0,
  }),
  project_source: define('project_source', ['projectSource'], 'project_source', {
    source_project_id: '',
    columns: [],
    source_type: null,
    datasource_config: null,
  }),
  file_source: define('file_source', ['fileSource'], 'file_source', {
    file_source_id: '',
    columns: [],
  }),
  transform: define('transform', [], 'transform', {
    transform_op: 'join',
    config: {},
  }),
  project_input: define('project_input', [], 'project_input', {
    input_key: '',
    label: 'Input',
    input_kind: null,
    description: '',
    required: true,
    default_value: null,
    multiple: false,
    expected_columns: [],
    validation: {},
    connection_set_id: null,
    order: 0,
    bindings: [],
  }),
  multi_join: define('multi_join', [], 'multi_join', {
    join_keys: [],
    join_key_ids: [],
    join_pairs: [],
    left_keys: [],
    right_keys: [],
    // Tipo letto quando manca, come il backend (utils/joinTypes, U21).
    multi_join_type: MISSING_JOIN_TYPE.multi_join,
  }),
  api_enrich: define('api_enrich', [], 'api_enrich', {
    api_id: '',
    api_name: '',
    param_mapping: {},
    parameters: null,
    output_mapping: [],
    enrich_type: 'manual',
    connection_type: null,
    exec_params: {},
    batch: {},
    continue_on_error: true,
    skip_null_params: true,
    max_errors: 0,
    api_type: null,
    static_parameters: {},
    request: null,
    response: null,
    extra_files: null,
    output_kind: null,
    execute_if: null,
    static_columns: [],
  }),
};

/**
 * Campi degli archi di join (Fase 5, T6, N6): tabella dichiarativa unica per
 * il salvataggio (canvasSerializer.serializeEdge) e il caricamento
 * (nodeFactories.createEdgeFromData), come i campi dei nodi. `keep`:
 * 'truthy' = il valore vale se non e' falsy (come prima), 'nullish' = vale
 * anche 0/'' (spatial_distance), 'present' = si salva solo se c'e' (non
 * aggiunge chiavi ai canvas che non l'hanno). Il round-trip di OGNI campo e'
 * nel test tests/shared/nodeTypeRoundTrip.contract.test.js.
 */
export const JOIN_EDGE_FIELDS = {
  join_type:          { default: MISSING_JOIN_TYPE.estrai, keep: 'truthy' },
  source_column:      { default: '', keep: 'truthy' },
  target_column:      { default: '', keep: 'truthy' },
  join_conditions:    { default: null, keep: 'truthy' },
  on_filters:         { default: null, keep: 'truthy' },
  is_spatial:         { default: false, keep: 'truthy' },
  spatial_predicate:  { default: null, keep: 'truthy' },
  source_geom_column: { default: null, keep: 'truthy' },
  target_geom_column: { default: null, keep: 'truthy' },
  spatial_distance:   { default: null, keep: 'nullish' },
  // Chiavi suggerite di un join nato da una relazione ArcGIS "da completare"
  // (utils/estraiJoins.relationJoinEnds): prima si perdevano al reload.
  suggestion:         { default: null, keep: 'present' },
};

/** Campi di un arco di join da `source` (data dell'arco o arco salvato). */
export function joinEdgeFields(source) {
  const out = {};
  for (const [key, field] of Object.entries(JOIN_EDGE_FIELDS)) {
    const value = source?.[key];
    if (field.keep === 'present') {
      if (value !== undefined && value !== null) out[key] = value;
    } else if (field.keep === 'nullish') {
      out[key] = value ?? field.default;
    } else {
      out[key] = value || field.default;
    }
  }
  return out;
}

const aliases = new Map(Object.values(NODE_TYPES).flatMap(entry =>
  [entry.type, ...entry.aliases].map(type => [type, entry.type])));

/**
 * Tipo di rendering di un nodo NUOVO (Fase 5, T6, R2.6): lo stesso che
 * produce il deserializer (reactFlowType del registro), cosi' nella stessa
 * sessione non convivono due forme dello stesso tipo. Eccezione dichiarata
 * del deserializer: l'Output di Manipola/Arricchisci resta `result`.
 */
export function renderNodeType(type) {
  return NODE_TYPES[canonicalNodeType(type)]?.reactFlowType ?? type;
}

export function canonicalNodeType(type) {
  return aliases.get(type) ?? type;
}

// Shared by both directions, including future backend access_denied_* flags.
export function backendAccessFlags(node) {
  return Object.fromEntries(Object.entries({ ...node, ...node.data }).filter(([key]) =>
    key === 'access_denied' || key.startsWith('access_denied_') || key === 'inaccessible_resource_type'));
}

// Campi SOLO di runtime/UI: mai inviati al backend. E' una lista di
// ESCLUSIONE: tutto il resto di node.data viene salvato. Con una allowlist
// un campo nuovo scritto da un componente e non registrato qui sparirebbe
// in silenzio al salvataggio (la classe di bug che R2 chiude): meglio un
// campo superfluo salvato che un dato perso.
// `_sourceName` (UI-3, U9): nome di una sorgente salvata col solo id,
// risolto dal catalogo all'apertura (components/editor/editorNodeCatalog).
export const TRANSIENT_DATA_KEYS = new Set(['availableColumns', 'inputColumns', '_advOpen', '_lastUpdate', '_sourceName']);

// COPIE DI UNA RISORSA (Fase 5, T6, R2.10, N11): dettagli che appartengono
// alla risorsa riferita dal nodo (la connessione API di `api_id`) e che il
// backend legge dalla risorsa, mai dal nodo. Non si salvano: i canvas vecchi
// che li contengono li perdono al primo salvataggio (auth mascherata,
// openapi_spec intero, endpoint), senza effetto sull'esecuzione.
export const RESOURCE_COPY_KEYS = {
  api_enrich: new Set(['endpoint', 'auth', 'openapi_spec']),
};
const isResourceCopyKey = (type, key) => Boolean(RESOURCE_COPY_KEYS[type]?.has(key));

// Colonne DERIVATE (Blocco A, regola 4): i nodi che calcolano le colonne
// dai loro ingressi non le tengono in `data.columns`. Le colonne vengono dal
// server (utils/columnSource) e al salvataggio si allegano dalla mappa
// (canvasSerializer, opzione derivedColumns), perche' il backend le legge
// ancora. Le sorgenti invece tengono `columns`: e' la loro configurazione.
export const DERIVED_COLUMNS_TYPES = new Set(['transform', 'multi_join', 'api_enrich', 'result']);
const isDerivedColumnsKey = (type, key) => key === 'columns' && DERIVED_COLUMNS_TYPES.has(type);

// I campi del registro garantiscono default e round-trip; gli altri campi
// presenti in node.data (non transitori) vengono conservati cosi' come sono.
export function persistedNodeData(node) {
  const type = canonicalNodeType(node.type);
  const entry = NODE_TYPES[type];
  const data = {};
  for (const [key, value] of Object.entries(node.data || {})) {
    if (!TRANSIENT_DATA_KEYS.has(key) && !isDerivedColumnsKey(type, key) && !isResourceCopyKey(type, key)) data[key] = value;
  }
  for (const [key, field] of Object.entries(entry?.fields || {})) {
    if (field.persisted) data[key] = nd(node, key, structuredClone(field.default));
  }
  // Le intestazioni segrete NON si tolgono qui (niente si toglie in
  // silenzio): un nodo che le ha e' "da migrare" e il salvataggio e'
  // bloccato da useCanvasStore (utils/restSecrets, assertNoRestSecrets).
  return { ...data, ...backendAccessFlags(node) };
}

// Toglie funzioni/simboli e i campi transitori anche negli oggetti annidati.
// I valori JSON-compatibili mantengono la loro forma.
export function persistenceValue(value) {
  if (typeof value === 'function' || typeof value === 'symbol') return undefined;
  if (Array.isArray(value)) return value.map(persistenceValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value)
      .filter(([key, item]) => !TRANSIENT_DATA_KEYS.has(key)
        && typeof item !== 'function' && typeof item !== 'symbol')
      .map(([key, item]) => [key, persistenceValue(item)]));
  }
  return value;
}
