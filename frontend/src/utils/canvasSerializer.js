import { nd } from './nodeHelpers';

/**
 * canvasSerializer.js
 *
 * Unica fonte di verità per la serializzazione del canvas prima del salvataggio.
 *
 * FORMATO UNIFICATO (Fase 5):
 * Tutti e tre gli editor (estrai, manipola, arricchisci) serializzano al
 * medesimo formato flat — nessun wrapper "data: {}".
 * I campi vivono direttamente sul nodo, esattamente come il modello Pydantic
 * Node in shared/models.py.
 *
 * Il parametro { mode } è mantenuto per compatibilità ma ignorato.
 */

const nodeSize = (node) => ({
  width:  node.style?.width  || node.data?.width  || node.width  || 250,
  height: node.style?.height || node.data?.height || node.height || 150,
});

const serializers = {

  tableNode: (node) => ({
    id: node.id, type: 'source', position: node.position,
    connection_id:      nd(node, 'connection_id',      null),
    table_name:         nd(node, 'table_name'),
    alias:              nd(node, 'alias',              null),
    columns:            nd(node, 'columns',            []),
    filters:            nd(node, 'filters',            []),
    calculated_columns: nd(node, 'calculated_columns', []),
  }),

  featureLayerNode: (node) => ({
    id: node.id, type: 'feature_source', position: node.position,
    layer_name:      node.data?.layer_name,
    layer_url:       node.data?.layer_url,
    connection_id:   node.data?.connection_id,
    connection_name: node.data?.connection_name,
    service_name:    node.data?.service_name,
    geometry_type:   node.data?.geometry_type,
    feature_count:   node.data?.feature_count  ?? null,
    where:           node.data?.where          ?? '',
    columns:         node.data?.columns        ?? [],
    relationships:   node.data?.relationships  ?? [],
  }),

  projectSource: (node) => ({
    id: node.id, type: 'project_source', position: node.position,
    source_project_id: nd(node, 'source_project_id'),
    columns:           nd(node, 'columns', []),
  }),

  fileSource: (node) => ({
    id: node.id, type: 'file_source', position: node.position,
    file_source_id: nd(node, 'file_source_id'),
    columns:        nd(node, 'columns', []),
  }),

  datasourceNode: (node) => ({
    id: node.id, type: 'datasource_node', position: node.position,
    mode:            nd(node, 'mode', 'source'),
    datasource_id:   nd(node, 'datasource_id', ''),
    datasource_name: nd(node, 'datasource_name', ''),
    // Sorgente
    file_mode:       nd(node, 'file_mode', 'fixed'),
    file_path:       nd(node, 'file_path', ''),
    folder_path:     nd(node, 'folder_path', ''),
    dynamic_rule:    nd(node, 'dynamic_rule', 'latest'),
    pattern:         nd(node, 'pattern', ''),
    format:          nd(node, 'format', ''),
    columns:         nd(node, 'columns', []),
    parse_options:   nd(node, 'parse_options', {}),
    // Carica
    dest_folder:     nd(node, 'dest_folder', ''),
    dest_filename:   nd(node, 'dest_filename', ''),
    dest_format:     nd(node, 'dest_format', 'csv'),
  }),

  transform: (node) => ({
    id: node.id, type: 'transform', position: node.position,
    transform_op: nd(node, 'transform_op'),
    config:       nd(node, 'config', {}),
    collapsed:    nd(node, 'collapsed', false),
  }),

  multi_join: (node) => ({
    id: node.id, type: 'multi_join', position: node.position,
    // join_keys e join_type stanno in data.config nel frontend
    join_keys:       node.data?.config?.join_keys ?? node.data?.join_keys ?? [],
    multi_join_type: node.data?.config?.join_type ?? node.data?.multi_join_type ?? 'inner',
  }),

  api_enrich: (node) => ({
    id: node.id, type: 'api_enrich', position: node.position,
    api_id:            nd(node, 'api_id'),
    api_name:          nd(node, 'api_name'),
    param_mapping:     nd(node, 'param_mapping', {}),
    columns:           nd(node, 'columns', []),
    enrich_type:       nd(node, 'enrich_type'),
    api_type:          nd(node, 'api_type'),
    connection_type:   nd(node, 'connection_type'),
    exec_params:       nd(node, 'exec_params', {}),
    endpoint:          nd(node, 'endpoint'),
    parameters:        nd(node, 'parameters'),
    output_mapping:    nd(node, 'output_mapping'),
    static_parameters: nd(node, 'static_parameters'),
    batch:             nd(node, 'batch', {}),
    continue_on_error: nd(node, 'continue_on_error', true),
    skip_null_params:  nd(node, 'skip_null_params', true),
    max_errors:        nd(node, 'max_errors', 0),
    execute_if:        nd(node, 'execute_if', null),
    // Processore File (rest_file): config multipart in/binary out
    request:           nd(node, 'request',  null),
    response:          nd(node, 'response', null),
    // File aggiuntivi caricati inline sul nodo (per file_fields con
    // source='extra_input'). Formato: {field_name: {file_source_id, ...}}
    extra_files:       nd(node, 'extra_files', null),
    // Tag opzionale che dice al canvas "lo zip prodotto da questo nodo
    // ha questa struttura nota" (es. 'imdf'). Il nodo result downstream
    // legge questo tag per specializzarsi.
    output_kind:       nd(node, 'output_kind', null),
  }),

  result: (node, { edges = [], allNodes = [] } = {}) => {
    const columns = _computeResultColumns(node, allNodes, edges);
    return {
      id: node.id, type: 'result', position: node.position,
      fileName:         nd(node, 'fileName',         'output'),
      format:           nd(node, 'format',            'csv'),
      distinct:         nd(node, 'distinct',          false),
      limit:            nd(node, 'limit',              null),
      geomColumn:       nd(node, 'geomColumn',         null),
      columns,
      group_by_enabled:  nd(node, 'group_by_enabled',  false),
      group_by_columns:  nd(node, 'group_by_columns',  []),
      aggregations:      nd(node, 'aggregations',       []),
      order_by_enabled:  nd(node, 'order_by_enabled',  false),
      order_by_columns:  nd(node, 'order_by_columns',  []),
      having_enabled:    nd(node, 'having_enabled',    false),
      having_conditions: nd(node, 'having_conditions', []),
      // Modalita' "Pubblicazione IMDF" (per ora dormienti; verranno
      // popolati quando aggiungeremo il publisher node — i campi sono
      // gia' nel modello Pydantic Node).
      imdf_arcgis_connection_id: nd(node, 'imdf_arcgis_connection_id', null),
      imdf_arcgis_folder:        nd(node, 'imdf_arcgis_folder',        null),
      imdf_fs_name_template:     nd(node, 'imdf_fs_name_template',     null),
      imdf_apply_renderers:      nd(node, 'imdf_apply_renderers',      false),
    };
  },

  carica: (node) => ({
    id: node.id, type: 'carica', position: node.position,
    dest_type:               nd(node, 'dest_type', 'db'),
    connection_id:           nd(node, 'connection_id'),
    schema_name:             nd(node, 'schema_name'),
    table_name:              nd(node, 'table_name'),
    write_mode:              nd(node, 'write_mode',              'create'),
    column_mappings:         nd(node, 'column_mappings',         []),
    match_columns:           nd(node, 'match_columns',           []),
    batch_size:              nd(node, 'batch_size',              1000),
    transaction_mode:        nd(node, 'transaction_mode',        'all_or_nothing'),
    create_spatial_index:    nd(node, 'create_spatial_index',    true),
    conflict_policy:         nd(node, 'conflict_policy',         'abort'),
    create_unique_index:     nd(node, 'create_unique_index',     false),
    unique_key_column:       nd(node, 'unique_key_column',       ''),
    rest_url:                nd(node, 'rest_url',                ''),
    rest_method:             nd(node, 'rest_method',             'POST'),
    rest_headers:            nd(node, 'rest_headers',            []),
    rest_body_mapping:       nd(node, 'rest_body_mapping',       []),
    rest_batch_mode:         nd(node, 'rest_batch_mode',         'batch'),
    rest_batch_size:         nd(node, 'rest_batch_size',         1000),
    rest_timeout_seconds:    nd(node, 'rest_timeout_seconds',    30),
    rest_success_statuses:   nd(node, 'rest_success_statuses',   [200, 201, 202, 204]),
  }),

  caricaArcgis: (node) => ({
    id: node.id, type: 'carica_arcgis', position: node.position,
    dest_type:               'arcgis',
    arcgis_connection_id:    nd(node, 'arcgis_connection_id'),
    arcgis_service_url:      nd(node, 'arcgis_service_url'),
    arcgis_layer_url:        nd(node, 'arcgis_layer_url'),
    arcgis_operation:        nd(node, 'arcgis_operation',        'add'),
    arcgis_folder_id:        nd(node, 'arcgis_folder_id',        ''),
    arcgis_match_column:     nd(node, 'arcgis_match_column',     ''),
    arcgis_new_service_name: nd(node, 'arcgis_new_service_name'),
    arcgis_new_layer_name:   nd(node, 'arcgis_new_layer_name',   'Layer 0'),
    column_mappings:         nd(node, 'column_mappings',         []),
  }),

  imdf_publisher: (node) => ({
    id: node.id, type: 'imdf_publisher', position: node.position,
    imdf_arcgis_connection_id: nd(node, 'imdf_arcgis_connection_id', null),
    imdf_arcgis_folder:        nd(node, 'imdf_arcgis_folder',        null),
    imdf_fs_name_template:     nd(node, 'imdf_fs_name_template',     null),
    imdf_apply_renderers:      nd(node, 'imdf_apply_renderers',      false),
    imdf_publish_mode:         nd(node, 'imdf_publish_mode',         'replace'),
  }),

  staticValues: (node) => ({
    id: node.id, type: 'staticValues', position: node.position,
    label:  nd(node, 'label',  'Valori Statici'),
    fields: nd(node, 'fields', []),
  }),

  comment: (node) => ({
    id: node.id, type: 'comment', position: node.position,
    ...nodeSize(node),
    level:      nd(node, 'level',       ''),
    label:      nd(node, 'label',       ''),
    title:      nd(node, 'title',       ''),
    color:      nd(node, 'color',       '#fbbf24'),
    arrowStyle: nd(node, 'arrowStyle',  null),
  }),
};

// alias nomi ReactFlow → tipo serializzato
serializers.resultNode     = serializers.result;
serializers.project_source = serializers.projectSource;
serializers.file_source    = serializers.fileSource;

export function serializeEdge(edge) {
  if (!edge.data || !edge.data.join_type) return edge;
  return {
    id: edge.id, source: edge.source, target: edge.target,
    join_type:          edge.data.join_type          || 'left',
    source_column:      edge.data.source_column      || '',
    target_column:      edge.data.target_column      || '',
    join_conditions:    edge.data.join_conditions    || null,
    on_filters:         edge.data.on_filters         || null,
    is_spatial:         edge.data.is_spatial         || false,
    spatial_predicate:  edge.data.spatial_predicate  || null,
    source_geom_column: edge.data.source_geom_column || null,
    target_geom_column: edge.data.target_geom_column || null,
    spatial_distance:   edge.data.spatial_distance   || null,
  };
}

export function serializeCanvas(nodes, edges, _options = {}) {
  const ctx = { edges, allNodes: nodes };
  const serializedNodes = nodes.map(node => {
    const s = serializers[node.type];
    return s ? s(node, ctx) : { id: node.id, type: node.type, position: node.position, ...(node.data || {}) };
  });
  return { nodes: serializedNodes, edges: edges.map(serializeEdge) };
}

function _computeResultColumns(resultNode, allNodes, edges) {
  // Fonte di verità: colonne del nodo upstream collegato al result.
  // Il vecchio comportamento privilegiava `resultNode.data.columns` anche quando stale,
  // persistendo colonne obsolete dopo modifiche al grafo (UI mostrava N colonne propagate
  // live via useMemo ma lo state del nodo restava fermo a uno stato precedente).
  //
  // C9 (backend): il server ricomputa autorevolmente result_node.columns
  // al PUT /canvas via SchemaInferenceService (vedi
  // backend/app/shared/canvas_columns.py). Quindi il valore calcolato qui
  // è best-effort per la UX live pre-save; quello che arriva al backend
  // può essere anche vuoto o stale — viene comunque sovrascritto lato
  // server con la BFS backward che copre tutti i node type
  // (source/transform/api_enrich/merge/join). Al reload la UI legge il
  // valore corretto dal canvas persistito.
  const saved = resultNode.data?.columns || [];
  const incomingEdge = edges.find(e => e.target === resultNode.id);
  if (!incomingEdge) return saved;
  const sourceNode = allNodes.find(n => n.id === incomingEdge.source);
  const upstream = sourceNode?.data?.columns || [];
  if (upstream.length === 0) return saved;
  // Merge: sincronizza nomi/tipi con l'upstream (rimuove colonne stale, aggiunge nuove)
  // ma preserva customizzazioni utente (alias, visible) del data.columns salvato.
  const savedByName = new Map(saved.map(c => [c.name, c]));
  return upstream.map(col => {
    const s = savedByName.get(col.name);
    return s ? { ...col, alias: s.alias ?? null, visible: s.visible ?? true } : col;
  });
}
